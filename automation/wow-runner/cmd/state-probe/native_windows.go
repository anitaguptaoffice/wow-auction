//go:build windows

package main

import (
	"bytes"
	"crypto/sha256"
	"debug/pe"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unsafe"

	"golang.org/x/arch/x86/x86asm"
	"golang.org/x/sys/windows"
)

// This is code/data-reference discovery, not execution of any client function.
func discoverNative(h windows.Handle, pid uint32, path, out, extra string) error {
	snap, e := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPMODULE|windows.TH32CS_SNAPMODULE32, pid)
	if e != nil {
		return e
	}
	defer windows.CloseHandle(snap)
	m := windows.ModuleEntry32{Size: uint32(unsafe.Sizeof(windows.ModuleEntry32{}))}
	if e = windows.Module32First(snap, &m); e != nil {
		return e
	}
	base := uintptr(m.ModBaseAddr)
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	hash := sha256.Sum256(b)
	f, e := pe.NewFile(bytes.NewReader(b))
	if e != nil {
		return e
	}
	defer f.Close()
	preferred := f.OptionalHeader.(*pe.OptionalHeader64).ImageBase
	words := []string{"GetSelectedCharacterGUID", "GetSelectedCharacter", "GetCharacterInfo", "GetCharacterGUID", "GetNumCharacters", "GetBasicCharacterInfo", "SetInCharacterSelect", "GetGlueScreen", "IsConnectedToServer", "GetServerName", "SelectCharacter"}
	targets := map[uint32]string{}
	for _, s := range f.Sections {
		data, _ := s.Data()
		for _, w := range words {
			needle := append([]byte(w), 0)
			for pos := 0; pos < len(data); {
				i := bytes.Index(data[pos:], needle)
				if i < 0 {
					break
				}
				i += pos
				pos = i + len(needle)
				targets[s.VirtualAddress+uint32(i)] = w
			}
		}
	}
	type functionRange struct{ Start, End, Unwind uint32 }
	var ranges []functionRange
	if s := f.Section(".pdata"); s != nil {
		data, _ := s.Data()
		for i := 0; i+12 <= len(data); i += 12 {
			ranges = append(ranges, functionRange{binary.LittleEndian.Uint32(data[i:]), binary.LittleEndian.Uint32(data[i+4:]), binary.LittleEndian.Uint32(data[i+8:])})
		}
	}
	findRange := func(rva uint32) *functionRange {
		i := sort.Search(len(ranges), func(i int) bool { return ranges[i].Start > rva }) - 1
		if i >= 0 && rva < ranges[i].End {
			return &ranges[i]
		}
		return nil
	}
	var refs []map[string]any
	// Absolute references in image data often identify native API registration pairs.
	for _, s := range f.Sections {
		if s.Characteristics&0x20000000 != 0 {
			continue
		}
		data, _ := s.Data()
		for i := 0; i+16 <= len(data); i += 8 {
			p := binary.LittleEndian.Uint64(data[i:])
			if p < preferred {
				continue
			}
			name, ok := targets[uint32(p-preferred)]
			if !ok || p-preferred > 0xffffffff {
				continue
			}
			next := binary.LittleEndian.Uint64(data[i+8:])
			entry := map[string]any{"kind": "data_pointer", "name": name, "rva": fmt.Sprintf("0x%x", s.VirtualAddress+uint32(i)), "nextValue": fmt.Sprintf("0x%x", next)}
			if next >= preferred && next-preferred < uint64(m.ModBaseSize) {
				entry["nextFunctionRange"] = findRange(uint32(next - preferred))
				entry["nextRva"] = fmt.Sprintf("0x%x", next-preferred)
			}
			refs = append(refs, entry)
		}
	}
	// Runtime .text references handle code that differs from the on-disk image.
	for _, s := range f.Sections {
		if s.Characteristics&0x20000000 == 0 {
			continue
		}
		for offset := uint32(0); offset < s.VirtualSize; offset += 1 << 20 {
			count := uint32(1<<20) + 6
			if count > s.VirtualSize-offset {
				count = s.VirtualSize - offset
			}
			data := read(h, base+uintptr(s.VirtualAddress+offset), int(count))
			if data == nil {
				continue
			}
			for i := 0; i+7 <= len(data) && i < 1<<20; i++ {
				if (data[i] != 0x48 && data[i] != 0x4c) || data[i+1] != 0x8d || data[i+2]&0xc7 != 5 {
					continue
				}
				rva := s.VirtualAddress + offset + uint32(i)
				target := int64(rva) + 7 + int64(int32(binary.LittleEndian.Uint32(data[i+3:])))
				name, ok := targets[uint32(target)]
				if !ok {
					continue
				}
				refs = append(refs, map[string]any{"kind": "runtime_lea", "name": name, "rva": fmt.Sprintf("0x%x", rva), "functionRange": findRange(rva)})
			}
		}
	}
	result := map[string]any{"pid": pid, "image": path, "sha256": hex.EncodeToString(hash[:]), "moduleBase": fmt.Sprintf("0x%x", base), "targets": targets, "references": refs, "scope": "Only exact named character-selection API strings, image data pointers and executable image code. No heap dump, credentials, process writes or calls into client."}
	var captures []map[string]any
	seen := map[uint32]bool{}
	if extra != "" {
		for _, s := range strings.Split(extra, ",") {
			rva, e := strconv.ParseUint(s, 0, 32)
			if e != nil {
				return e
			}
			refs = append(refs, map[string]any{"kind": "explicit_native_function", "functionRange": findRange(uint32(rva))})
		}
	}
	for _, ref := range refs {
		fr, _ := ref["functionRange"].(*functionRange)
		if fr == nil {
			fr, _ = ref["nextFunctionRange"].(*functionRange)
		}
		if fr == nil || seen[fr.Start] || fr.End <= fr.Start || fr.End-fr.Start > 16384 {
			continue
		}
		seen[fr.Start] = true
		data := read(h, base+uintptr(fr.Start), int(fr.End-fr.Start))
		if data == nil {
			continue
		}
		codePath := filepath.Join(filepath.Dir(out), fmt.Sprintf("code-%x.bin", fr.Start))
		// Bounded function code only, for an offline IDA copy.
		if e := os.WriteFile(codePath, data, 0600); e != nil {
			return e
		}
		var instructions []map[string]any
		for i := 0; i < len(data); {
			inst, err := x86asm.Decode(data[i:], 64)
			if err != nil || inst.Len == 0 {
				break
			}
			address := preferred + uint64(fr.Start) + uint64(i)
			instructions = append(instructions, map[string]any{"addr": fmt.Sprintf("0x%x", address), "text": x86asm.IntelSyntax(inst, address, nil)})
			i += inst.Len
		}
		captures = append(captures, map[string]any{"rva": fr.Start, "size": len(data), "file": codePath, "instructions": instructions})
	}
	result["codeCaptures"] = captures
	file, e := os.OpenFile(out, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	defer file.Close()
	enc := json.NewEncoder(file)
	enc.SetIndent("", "  ")
	if e = enc.Encode(result); e != nil {
		return e
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"report": out, "captures": len(captures), "references": len(refs)})
}
