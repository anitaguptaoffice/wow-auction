//go:build windows

package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"hash/adler32"
	"math"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
	"unsafe"

	"golang.org/x/sys/windows"
)

type addonFrame struct {
	Session string
	Seq     uint64
	Values  map[string]string
}

func parseStatus(b []byte) (addonFrame, error) {
	f := addonFrame{}
	if len(b) > 2048 || !utf8.Valid(b) || !bytes.HasSuffix(b, []byte{'\n'}) {
		return f, fmt.Errorf("invalid status size/encoding")
	}
	s := strings.TrimSuffix(string(b), "\n")
	i := strings.LastIndex(s, "|adler=")
	if i < 0 {
		return f, fmt.Errorf("missing status checksum")
	}
	n, e := strconv.ParseUint(s[i+7:], 10, 32)
	if e != nil || uint32(n) != adler32.Checksum([]byte(s[:i])) {
		return f, fmt.Errorf("status checksum mismatch")
	}
	p := strings.Split(s[:i], "|")
	if len(p) < 6 || p[0] != "WAST1" || p[1] == "" {
		return f, fmt.Errorf("invalid status header")
	}
	f.Session = p[1]
	f.Seq, e = strconv.ParseUint(p[2], 10, 64)
	if e != nil || f.Seq == 0 {
		return f, fmt.Errorf("invalid sequence")
	}
	f.Values = map[string]string{}
	for _, pair := range p[5:] {
		key, val, ok := strings.Cut(pair, "=")
		if _, exists := f.Values[key]; !ok || exists || key == "" {
			return f, fmt.Errorf("invalid/duplicate status field")
		}
		val, e = url.PathUnescape(val)
		if e != nil {
			return f, e
		}
		f.Values[key] = val
	}
	return f, nil
}

type statusBinding struct {
	slot, skip uintptr
	session    string
	seq        uint64
	changed    time.Time
}

func readStatus(h windows.Handle, slot, skip uintptr) (addonFrame, error) {
	p := read(h, slot, 8)
	if p == nil {
		return addonFrame{}, fmt.Errorf("status pointer unavailable")
	}
	ptr := binary.LittleEndian.Uint64(p)
	if ptr < 0x10000 || ptr > 0x7fffffffffff {
		return addonFrame{}, fmt.Errorf("invalid status pointer")
	}
	// Read only the small addon-owned frame, never save unrelated heap bytes.
	var packet []byte
	for offset := 0; offset < 2048; offset += 64 {
		part := read(h, uintptr(ptr)+skip+uintptr(offset), 64)
		if part == nil {
			return addonFrame{}, fmt.Errorf("status frame unavailable")
		}
		if i := bytes.IndexByte(part, '\n'); i >= 0 {
			packet = append(packet, part[:i+1]...)
			if !bytes.Equal(p, read(h, slot, 8)) || !bytes.Equal(packet, read(h, uintptr(ptr)+skip, len(packet))) {
				return addonFrame{}, fmt.Errorf("status changed during read")
			}
			return parseStatus(packet)
		}
		packet = append(packet, part...)
		if offset == 0 && !bytes.HasPrefix(packet, []byte("WAST1|")) {
			break
		}
	}
	return addonFrame{}, fmt.Errorf("status terminator unavailable")
}

func pause(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func (b *statusBinding) fresh(ctx context.Context, h windows.Handle) (addonFrame, error) {
	return b.next(ctx, func() (addonFrame, error) { return readStatus(h, b.slot, b.skip) })
}

func (b *statusBinding) next(ctx context.Context, sample func() (addonFrame, error)) (addonFrame, error) {
	for {
		f, e := sample()
		if e != nil {
			// Publishing replaces the pointer atomically. A racing read is not a
			// stopped heartbeat: discard it and retry inside the same stale bound.
			if time.Since(b.changed) > 5*time.Second {
				return f, e
			}
			if err := pause(ctx, 25*time.Millisecond); err != nil {
				return f, err
			}
			continue
		}
		if f.Session != b.session || f.Seq < b.seq {
			return f, fmt.Errorf("addon session changed")
		}
		if f.Seq > b.seq {
			b.seq = f.Seq
			b.changed = time.Now()
			return f, nil
		}
		if time.Since(b.changed) > 5*time.Second {
			return f, fmt.Errorf("addon heartbeat stopped")
		}
		if e := pause(ctx, 50*time.Millisecond); e != nil {
			return f, e
		}
	}
}

func locateStatus(ctx context.Context, h windows.Handle) (bindingResult *statusBinding, frameResult addonFrame, resultErr error) {
	var scanned uint64
	var yieldedAt uint64
	anchors, frames, heartbeats := 0, 0, 0
	defer func() {
		if resultErr != nil {
			resultErr = fmt.Errorf("%w (MiB=%d anchors=%d frames=%d heartbeats=%d)", resultErr, scanned>>20, anchors, frames, heartbeats)
		}
	}()
	var regions []region
	for addr := uintptr(0); addr < 0x7fffffffffff; {
		if ctx.Err() != nil {
			return nil, addonFrame{}, ctx.Err()
		}
		var m windows.MemoryBasicInformation
		if windows.VirtualQueryEx(h, addr, &m, unsafe.Sizeof(m)) != nil {
			break
		}
		end := m.BaseAddress + m.RegionSize
		if end <= addr {
			break
		}
		if m.State == windows.MEM_COMMIT && m.Type == 0x20000 && (m.Protect == 2 || m.Protect == 4 || m.Protect == 8) {
			regions = append(regions, region{m.BaseAddress, m.RegionSize})
		}
		addr = end
	}
	sort.Slice(regions, func(i, j int) bool { return regions[i].size < regions[j].size })
	magic := make([]byte, 8)
	binary.LittleEndian.PutUint64(magic, math.Float64bits(8675309123981))
	checked := map[uintptr]bool{}
	for _, r := range regions {
		var tail []byte
		for offset := uintptr(0); offset < r.size; {
			if ctx.Err() != nil {
				return nil, addonFrame{}, ctx.Err()
			}
			if scanned >= 16<<30 {
				return nil, addonFrame{}, fmt.Errorf("status discovery byte limit")
			}
			size := min(uintptr(1<<20), r.size-offset)
			buf := read(h, r.base+offset, int(size))
			scanned += uint64(size)
			base := r.base + offset - uintptr(len(tail))
			offset += size
			if buf == nil {
				tail = nil
				continue
			}
			data := append(tail, buf...)
			for pos := 0; pos < len(data); {
				i := bytes.Index(data[pos:], magic)
				if i < 0 {
					break
				}
				i += pos
				pos = i + 8
				anchor := base + uintptr(i)
				if anchor%8 != 0 {
					continue
				}
				anchors++
				for slot := anchor - 128; slot < anchor+264; slot += 8 {
					if checked[slot] {
						continue
					}
					checked[slot] = true
					for _, skip := range []uintptr{32, 24, 16, 40, 48, 0} {
						f, e := readStatus(h, slot, skip)
						if e != nil {
							continue
						}
						frames++
						binding := &statusBinding{slot, skip, f.Session, f.Seq, time.Now()}
						probe, cancel := context.WithTimeout(ctx, 2200*time.Millisecond)
						fresh, e := binding.fresh(probe, h)
						cancel()
						if e == nil {
							heartbeats++
						}
						if e == nil && fresh.Values["world"] == "1" {
							return binding, fresh, nil
						}
					}
				}
			}
			tail = append([]byte(nil), data[len(data)-min(7, len(data)):]...)
			// Pace bytes, not virtual regions: thousands of 4 KiB regions would
			// otherwise turn a 1 ms Windows timer into minutes before the Lua heap.
			if scanned-yieldedAt >= 8<<20 {
				yieldedAt = scanned
				if e := pause(ctx, time.Millisecond); e != nil {
					return nil, addonFrame{}, e
				}
			}
		}
	}
	return nil, addonFrame{}, fmt.Errorf("no live addon status found")
}
