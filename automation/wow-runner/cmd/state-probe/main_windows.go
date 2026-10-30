//go:build windows

// state-probe is a bounded, read-only discovery experiment, not an automation gate.
// It records exact requested strings and candidate addresses only, never a dump.
package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
)

type hit struct {
	Address       uint64   `json:"address"`
	Encoding      string   `json:"encoding"`
	LuaCandidates []uint64 `json:"luaCandidates,omitempty"`
}
type report struct {
	PID      uint             `json:"pid"`
	Image    string           `json:"image"`
	Started  string           `json:"started"`
	Access   string           `json:"access"`
	Bytes    uint64           `json:"bytesRead"`
	Failures int              `json:"failedReads"`
	Seconds  float64          `json:"seconds"`
	Complete bool             `json:"searchComplete"`
	Hits     map[string][]hit `json:"hits"`
	Limit    string           `json:"limitation"`
}
type region struct{ base, size uintptr }

func read(h windows.Handle, address uintptr, size int) []byte {
	b := make([]byte, size)
	var n uintptr
	if windows.ReadProcessMemory(h, address, &b[0], uintptr(size), &n) != nil || n != uintptr(size) {
		return nil
	}
	return b
}

func main() {
	pid := flag.Uint("pid", 0, "explicit WoW PID")
	exe := flag.String("exe", "", "expected full executable path")
	character := flag.String("character", "", "user-observed character name")
	realm := flag.String("realm", "", "user-observed realm")
	out := flag.String("out", "", "new JSON report path")
	limit := flag.Duration("limit", 30*time.Second, "bounded scan duration")
	native := flag.Bool("native", false, "discover native character-selection API registration/code references only")
	functions := flag.String("functions", "", "additional native function RVAs from previous disassembly, comma-separated")
	snapshot := flag.Bool("snapshot", false, "read version-pinned native character selection fields")
	discoverStrings := flag.Bool("discover-strings", false, "explicit initial string discovery; not needed for normal state reads")
	key := flag.String("key", "", "explicit single background selection test: Down or Up (never Enter)")
	expectGUID := flag.String("expect-guid", "", "required currently selected GUID for a single key test")
	collect := flag.String("collect-config", "", "explicit background collection run with JSON config; includes client entry and per-character archive")
	addonStatus := flag.Bool("addon-status", false, "read only: locate a fresh addon status frame")
	flag.Parse()
	if *collect != "" {
		ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
		defer cancel()
		if err := runCollection(ctx, *collect); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *pid == 0 || *exe == "" || *character == "" || *realm == "" || *out == "" {
		panic("pid, exe, character, realm and out required")
	}
	if *limit <= 0 || *limit > time.Minute {
		panic("limit must be in (0, 60s]")
	}
	if (*native && *snapshot) || (*discoverStrings && (*native || *snapshot)) {
		panic("choose one mode")
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_INFORMATION|windows.PROCESS_VM_READ, false, uint32(*pid))
	if err != nil {
		panic(err)
	}
	defer windows.CloseHandle(h)
	path := make([]uint16, 32768)
	n := uint32(len(path))
	if err := windows.QueryFullProcessImageName(h, 0, &path[0], &n); err != nil {
		panic(err)
	}
	actual := windows.UTF16ToString(path[:n])
	if !strings.EqualFold(filepath.Clean(actual), filepath.Clean(*exe)) {
		panic("PID/executable mismatch")
	}
	if *addonStatus {
		ctx, cancel := context.WithTimeout(context.Background(), *limit)
		defer cancel()
		_, f, e := locateStatus(ctx, h)
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
		if err := json.NewEncoder(os.Stdout).Encode(f); err != nil {
			panic(err)
		}
		return
	}
	if *key != "" {
		if err := selectionKeyStep(h, uint32(*pid), actual, *character, *realm, *out, *key, *expectGUID); err != nil {
			panic(err)
		}
		return
	}
	if *snapshot || (!*native && !*discoverStrings) {
		if err := snapshotNative(h, uint32(*pid), actual, *character, *realm, *out); err != nil {
			panic(err)
		}
		return
	}
	if *native {
		if err := discoverNative(h, uint32(*pid), actual, *out, *functions); err != nil {
			panic(err)
		}
		return
	}
	r := report{PID: *pid, Image: actual, Started: time.Now().Format(time.RFC3339Nano), Access: "PROCESS_QUERY_INFORMATION | PROCESS_VM_READ", Hits: map[string][]hit{}, Limit: "String matches and Lua header candidates are NOT proof of the current screen or selected character. No input, writes, injection or debugger attachment."}
	patterns := map[string][]byte{}
	for _, s := range []string{*character, *realm, "CharacterSelect", "selectedIndex", "waitingforCharacterList", "retrievingCharacters", "GlueParent", "currentScreen", "charselect", "GetCharacterInfo", "GetCharacterGUID", "GetSelectedCharacter", "GetBasicCharacterInfo"} {
		patterns[s+"/utf8"] = []byte(s)
	}
	for _, s := range []string{*character, *realm} {
		u := utf16.Encode([]rune(s))
		b := make([]byte, len(u)*2)
		for i, v := range u {
			binary.LittleEndian.PutUint16(b[i*2:], v)
		}
		patterns[s+"/utf16"] = b
	}
	start := time.Now()
	var regions []region
	for address := uintptr(0); address < 0x7fffffffffff; {
		var info windows.MemoryBasicInformation
		if windows.VirtualQueryEx(h, address, &info, unsafe.Sizeof(info)) != nil {
			break
		}
		end := info.BaseAddress + info.RegionSize
		if end <= address {
			break
		}
		if info.State == windows.MEM_COMMIT && info.Type == 0x20000 && (info.Protect == windows.PAGE_READONLY || info.Protect == windows.PAGE_READWRITE || info.Protect == windows.PAGE_WRITECOPY) {
			regions = append(regions, region{info.BaseAddress, info.RegionSize})
		}
		address = end
	}
	sort.Slice(regions, func(i, j int) bool { return regions[i].size < regions[j].size })
	fmt.Printf("read-only opened PID %d, %d private readable regions\n", *pid, len(regions))
	r.Complete = true
scan:
	for _, reg := range regions {
		var tail []byte
		for offset := uintptr(0); offset < reg.size; {
			if time.Since(start) > *limit || r.Bytes >= 16<<30 {
				r.Complete = false
				break scan
			}
			size := uintptr(1 << 20)
			if size > reg.size-offset {
				size = reg.size - offset
			}
			b := read(h, reg.base+offset, int(size))
			offset += size
			if b == nil {
				r.Failures++
				tail = nil
				continue
			}
			r.Bytes += uint64(len(b))
			data := append(tail, b...)
			base := reg.base + offset - size - uintptr(len(tail))
			for key, needle := range patterns {
				for pos := 0; pos < len(data) && len(r.Hits[key]) < 128; {
					idx := bytes.Index(data[pos:], needle)
					if idx < 0 {
						break
					}
					idx += pos
					pos = idx + len(needle)
					// A hit wholly inside the overlap was already recorded.
					if idx+len(needle) <= len(tail) {
						continue
					}
					address := base + uintptr(idx)
					item := hit{Address: uint64(address), Encoding: "utf8"}
					if strings.HasSuffix(key, "/utf16") {
						item.Encoding = "utf16"
					} else {
						// Candidate Lua TString layouts: require exact length, string tag,
						// NUL terminator and aligned object. Still not a live-root proof.
						for _, header := range []uintptr{24, 32, 40, 48} {
							if address < header || (address-header)%8 != 0 {
								continue
							}
							v := read(h, address-header, int(header)+len(needle)+1)
							if len(v) == 0 || v[8] != 4 || v[len(v)-1] != 0 {
								continue
							}
							if binary.LittleEndian.Uint64(v[header-8:header]) == uint64(len(needle)) {
								item.LuaCandidates = append(item.LuaCandidates, uint64(address-header))
							}
						}
					}
					r.Hits[key] = append(r.Hits[key], item)
				}
			}
			keep := 128
			if len(data) < keep {
				keep = len(data)
			}
			tail = append([]byte(nil), data[len(data)-keep:]...)
		}
	}
	r.Seconds = time.Since(start).Seconds()
	if err := os.MkdirAll(filepath.Dir(*out), 0700); err != nil {
		panic(err)
	}
	f, err := os.OpenFile(*out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		panic(err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(r); err != nil {
		panic(err)
	}
	counts := map[string]int{}
	for k, v := range r.Hits {
		counts[k] = len(v)
	}
	json.NewEncoder(os.Stdout).Encode(map[string]any{"report": *out, "seconds": r.Seconds, "bytes": r.Bytes, "complete": r.Complete, "counts": counts})
}
