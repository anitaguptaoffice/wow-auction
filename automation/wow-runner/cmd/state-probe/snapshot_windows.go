//go:build windows

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"time"
	"unicode/utf8"
	"unsafe"

	"golang.org/x/sys/windows"
)

const supportedSHA = "b9739df5d0c973c486b618faa50dc0d60a747441ff713cd7c46234daa447071a"

type characterRow struct {
	Slot      int    `json:"nativeSlotZeroBased"`
	GUID      string `json:"guid128"`
	Name      string `json:"name"`
	RealmID   uint64 `json:"guidRealmIdCandidate"`
	RealmKey  uint32 `json:"realmLookupKey"`
	RealmName string `json:"realmName"`
	Selected  bool   `json:"selected"`
}
type selectionSnapshot struct {
	InCharacterSelect bool           `json:"inCharacterSelect"`
	SelectedSlot      int32          `json:"selectedNativeSlotZeroBased"`
	SlotCount         uint64         `json:"nativeSlotCount"`
	Rows              []characterRow `json:"characters"`
}

func mixGUID(v uint64) uint64 {
	v ^= v >> 33
	v *= 0xff51afd7ed558ccd
	v ^= v >> 33
	v *= 0xc4ceb9fe1a85ec53
	return v ^ (v >> 33)
}

func snapshotNative(h windows.Handle, pid uint32, path, target, targetRealm, out string) error {
	return nativeObservation(h, pid, path, target, targetRealm, func(result map[string]any) error {
		file, err := os.OpenFile(out, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		defer file.Close()
		enc := json.NewEncoder(file)
		enc.SetIndent("", "  ")
		if err := enc.Encode(result); err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(result)
	})
}

func nativeObservation(h windows.Handle, pid uint32, path, target, targetRealm string, publish func(map[string]any) error) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(b)
	if hex.EncodeToString(digest[:]) != supportedSHA {
		return fmt.Errorf("unsupported executable: no state read")
	}
	sh, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPMODULE|windows.TH32CS_SNAPMODULE32, pid)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(sh)
	m := windows.ModuleEntry32{Size: uint32(unsafe.Sizeof(windows.ModuleEntry32{}))}
	if err = windows.Module32First(sh, &m); err != nil {
		return err
	}
	base := uintptr(m.ModBaseAddr)
	// Fixed RVAs derived from captured 70575 native code, never persisted absolute pointers.
	readAt := func(address uintptr, n int) ([]byte, error) {
		b := read(h, address, n)
		if b == nil {
			return nil, fmt.Errorf("unreadable state address 0x%x", address)
		}
		return b, nil
	}
	qword := func(address uintptr) (uint64, error) {
		b, e := readAt(address, 8)
		if e != nil {
			return 0, e
		}
		return binary.LittleEndian.Uint64(b), nil
	}
	sample := func() (selectionSnapshot, error) {
		s := selectionSnapshot{SelectedSlot: -1}
		flag, e := readAt(base+0x608136d, 1)
		if e != nil {
			return s, e
		}
		if flag[0] > 1 {
			return s, fmt.Errorf("invalid screen flag")
		}
		s.InCharacterSelect = flag[0] == 1
		if !s.InCharacterSelect {
			return s, nil
		}
		sel, e := readAt(base+0x5d6e518, 4)
		if e != nil {
			return s, e
		}
		s.SelectedSlot = int32(binary.LittleEndian.Uint32(sel))
		vector, e := qword(base + 0x823fe20)
		if e != nil {
			return s, e
		}
		count, e := qword(base + 0x823fe28)
		if e != nil {
			return s, e
		}
		s.SlotCount = count
		if count == 0 || count > 256 || vector < 0x10000 || s.SelectedSlot < 0 || uint64(s.SelectedSlot) >= count {
			return s, fmt.Errorf("list/selection unavailable or outside limits")
		}
		list, e := readAt(uintptr(vector), int(count)*16)
		if e != nil {
			return s, e
		}
		buckets, e := qword(base + 0x5e0f268)
		if e != nil {
			return s, e
		}
		bucketCount, e := qword(base + 0x5e0f260)
		if e != nil {
			return s, e
		}
		if buckets < 0x10000 || bucketCount == 0 || bucketCount > 1<<20 || bucketCount&(bucketCount-1) != 0 {
			return s, fmt.Errorf("invalid character map")
		}
		realmBuckets, e := qword(base + 0x5dcb9e0)
		if e != nil {
			return s, e
		}
		realmCount, e := qword(base + 0x5dcb9d8)
		if e != nil {
			return s, e
		}
		if realmBuckets < 0x10000 || realmCount == 0 || realmCount > 1<<20 || realmCount&(realmCount-1) != 0 {
			return s, fmt.Errorf("invalid realm cache")
		}
		realmNames := map[uint32]string{}
		lookupRealm := func(key uint32) (string, error) {
			if name, ok := realmNames[key]; ok {
				return name, nil
			}
			node, e := qword(uintptr(realmBuckets + 8*(mixGUID(uint64(key))&(realmCount-1))))
			if e != nil {
				return "", e
			}
			visited := map[uint64]bool{}
			for depth := 0; node != 0 && depth < 128; depth++ {
				if node < 0x10000 || visited[node] {
					return "", fmt.Errorf("invalid realm cache chain")
				}
				visited[node] = true
				n, e := readAt(uintptr(node), 16)
				if e != nil {
					return "", e
				}
				if binary.LittleEndian.Uint32(n[8:]) == key {
					flags, e := readAt(uintptr(node+16+584), 4)
					if e != nil {
						return "", e
					}
					if binary.LittleEndian.Uint32(flags)&1 == 0 {
						return "", fmt.Errorf("realm name not loaded")
					}
					text, e := readAt(uintptr(node+24), 256)
					if e != nil {
						return "", e
					}
					end := bytes.IndexByte(text, 0)
					if end < 1 || !utf8.Valid(text[:end]) {
						return "", fmt.Errorf("invalid realm name")
					}
					name := string(text[:end])
					realmNames[key] = name
					return name, nil
				}
				node = binary.LittleEndian.Uint64(n)
			}
			return "", fmt.Errorf("realm %d absent from cache", key)
		}
		for i := 0; i < int(count); i++ {
			guid := list[i*16 : (i+1)*16]
			lo := binary.LittleEndian.Uint64(guid)
			hi := binary.LittleEndian.Uint64(guid[8:])
			if lo|hi == 0 {
				continue
			}
			bucket := (mixGUID(lo+hi) + mixGUID(lo+hi+hi)) & (bucketCount - 1)
			node, e := qword(uintptr(buckets + 8*bucket))
			if e != nil {
				return s, e
			}
			found := false
			visited := map[uint64]bool{}
			for depth := 0; node != 0 && depth < 128; depth++ {
				if node < 0x10000 || visited[node] {
					return s, fmt.Errorf("invalid/cyclic character chain")
				}
				visited[node] = true
				n, e := readAt(uintptr(node), 48)
				if e != nil {
					return s, e
				}
				if bytes.Equal(n[8:24], guid) {
					ptr := binary.LittleEndian.Uint64(n[40:])
					if ptr < 0x10000 {
						return s, fmt.Errorf("character record unavailable")
					}
					record, e := readAt(uintptr(ptr), 74)
					if e != nil {
						return s, e
					}
					if !bytes.Equal(record[:16], guid) {
						return s, fmt.Errorf("character GUID mismatch")
					}
					name := record[16:68]
					end := bytes.IndexByte(name, 0)
					if end < 1 || !utf8.Valid(name[:end]) {
						return s, fmt.Errorf("invalid character name")
					}
					realmKey := binary.LittleEndian.Uint32(record[68:72])
					realmName, e := lookupRealm(realmKey)
					if e != nil {
						return s, e
					}
					s.Rows = append(s.Rows, characterRow{Slot: i, GUID: fmt.Sprintf("%016x%016x", hi, lo), Name: string(name[:end]), RealmID: (hi >> 42) & 0xffff, RealmKey: realmKey, RealmName: realmName, Selected: int32(i) == s.SelectedSlot})
					found = true
					break
				}
				node = binary.LittleEndian.Uint64(n)
			}
			if !found {
				return s, fmt.Errorf("listed character missing from map")
			}
		}
		return s, nil
	}
	start := time.Now()
	first, err := sample()
	if err != nil {
		return err
	}
	time.Sleep(150 * time.Millisecond)
	second, err := sample()
	if err != nil {
		return err
	}
	x, _ := json.Marshal(first)
	y, _ := json.Marshal(second)
	if !bytes.Equal(x, y) {
		return fmt.Errorf("state changed during observation; no result published")
	}
	var selected *characterRow
	var targetRows []characterRow
	var matches []int
	for i := range second.Rows {
		row := &second.Rows[i]
		if row.Selected {
			selected = row
		}
		if row.Name == target {
			matches = append(matches, row.Slot)
			if row.RealmName == targetRealm {
				targetRows = append(targetRows, *row)
			}
		}
	}
	result := map[string]any{"pid": pid, "observedAt": time.Now().Format(time.RFC3339Nano), "imageSHA256": supportedSHA, "snapshot": second, "selectedCharacter": selected, "targetNameMatches": matches, "targetMatches": targetRows, "targetUnique": len(targetRows) == 1, "consistentSamples": 2, "elapsedMs": time.Since(start).Milliseconds(), "readyForAutomation": false, "limitations": []string{"Native array slot is not proven equivalent to visible UI row or keyboard order.", "Realm display names come from the client realm cache; GUID realm ID and realm lookup key are distinct fields.", "List-loading/error gates and changed-module-base validation are still required. A false character-select flag alone does not prove in-world state."}}
	return publish(result)
}
