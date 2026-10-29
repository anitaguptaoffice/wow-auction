//go:build windows

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
	"wow-auction/automation/wow-runner/internal/snapshot"
	"wow-auction/automation/wow-runner/internal/syncqueue"
	"wow-auction/automation/wow-runner/internal/winutil"
)

type collectCharacter struct {
	Name          string `json:"name"`
	Realm         string `json:"realm"`
	Source        string `json:"source"`
	ResumeSession string `json:"resumeSession,omitempty"`
	ResumeRun     string `json:"resumeRun,omitempty"`
}
type collectConfig struct {
	Exe                  string             `json:"exe"`
	LaunchCommand        []string           `json:"launchCommand"`
	Output               string             `json:"output"`
	Characters           []collectCharacter `json:"characters"`
	ScanTimeoutSeconds   int                `json:"scanTimeoutSeconds"`
	NoResponseSeconds    int                `json:"noResponseSeconds"`
	CooldownSeconds      int                `json:"cooldownSeconds"`
	MaxCooldownRetries   *int               `json:"maxCooldownRetries"`
	CycleIntervalSeconds int                `json:"cycleIntervalSeconds,omitempty"`
	CycleStart           time.Time          `json:"-"`
	Sync                 *syncqueue.Config  `json:"sync,omitempty"`
}
type collector struct {
	ctx            context.Context
	config         collectConfig
	h              windows.Handle
	pid            uint32
	hwnd           uintptr
	status         *statusBinding
	character      collectCharacter
	log            *json.Encoder
	last           addonFrame
	cycleStartedAt time.Time
}

func loadCollectConfig(path string) (collectConfig, error) {
	var cfg collectConfig
	f, e := os.Open(path)
	if e != nil {
		return cfg, e
	}
	defer f.Close()
	d := json.NewDecoder(f)
	d.DisallowUnknownFields()
	if e = d.Decode(&cfg); e != nil {
		return cfg, e
	}
	if !filepath.IsAbs(cfg.Exe) || !filepath.IsAbs(cfg.Output) || len(cfg.Characters) == 0 {
		return cfg, fmt.Errorf("absolute exe/output and characters required")
	}
	seen := map[string]bool{}
	for _, ch := range cfg.Characters {
		if cfg.CycleIntervalSeconds > 0 && (ch.ResumeRun != "" || ch.ResumeSession != "") {
			return cfg, fmt.Errorf("scheduled loops cannot use a single-run resume token")
		}
		if (ch.ResumeSession == "") != (ch.ResumeRun == "") {
			return cfg, fmt.Errorf("resume requires both session and run")
		}
		if ch.Name == "" || ch.Realm == "" || !filepath.IsAbs(ch.Source) || filepath.Base(ch.Source) != "AuctionSearchExample.lua" {
			return cfg, fmt.Errorf("character name, realm and exact SavedVariables source required")
		}
		identity := ch.Name + "|" + ch.Realm
		if seen[identity] {
			return cfg, fmt.Errorf("duplicate character")
		}
		seen[identity] = true
		if !strings.EqualFold(filepath.Base(filepath.Dir(ch.Source)), "SavedVariables") || filepath.Base(filepath.Dir(filepath.Dir(ch.Source))) != ch.Name || filepath.Base(filepath.Dir(filepath.Dir(filepath.Dir(ch.Source)))) != ch.Realm {
			return cfg, fmt.Errorf("source path does not match character/realm")
		}
	}
	if cfg.ScanTimeoutSeconds == 0 {
		cfg.ScanTimeoutSeconds = 240
	}
	if cfg.ScanTimeoutSeconds < 10 || cfg.ScanTimeoutSeconds > 900 {
		return cfg, fmt.Errorf("scan timeout must be 10..900 seconds")
	}
	if cfg.NoResponseSeconds == 0 {
		cfg.NoResponseSeconds = 60
	}
	if cfg.CooldownSeconds == 0 {
		cfg.CooldownSeconds = 900
	}
	if cfg.MaxCooldownRetries == nil {
		n := 2
		cfg.MaxCooldownRetries = &n
	}
	if cfg.NoResponseSeconds < 60 || cfg.NoResponseSeconds > 300 || cfg.CooldownSeconds < 900 || cfg.CooldownSeconds > 3600 || *cfg.MaxCooldownRetries < 0 || *cfg.MaxCooldownRetries > 5 {
		return cfg, fmt.Errorf("invalid cooldown settings: wait >=60s, cooldown >=900s, retries 0..5")
	}
	if len(cfg.LaunchCommand) > 0 && !filepath.IsAbs(cfg.LaunchCommand[0]) {
		return cfg, fmt.Errorf("launch command must use an absolute executable path")
	}
	if cfg.CycleIntervalSeconds != 0 && (cfg.CycleIntervalSeconds < 900 || cfg.CycleIntervalSeconds > 604800) {
		return cfg, fmt.Errorf("cycle interval must be 900..604800 seconds")
	}
	if cfg.Sync != nil && cfg.Sync.Enabled {
		if !filepath.IsAbs(cfg.Sync.QueueDir) || !filepath.IsAbs(cfg.Sync.TokenFile) || !strings.HasPrefix(cfg.Sync.Server, "https://") {
			return cfg, fmt.Errorf("sync requires HTTPS server and absolute token/queue paths")
		}
		if cfg.CycleIntervalSeconds == 0 {
			return cfg, fmt.Errorf("automatic sync requires a persistent scheduled controller")
		}
		cfg.Sync.ScanRoot = cfg.Output
	}
	return cfg, nil
}

func (c *collector) event(state string, fields map[string]any) error {
	if fields == nil {
		fields = map[string]any{}
	}
	fields["state"] = state
	fields["time"] = time.Now().Format(time.RFC3339Nano)
	fields["character"] = c.character.Name
	fields["realm"] = c.character.Realm
	fmt.Println(state, c.character.Name, c.character.Realm)
	if remaining, ok := fields["remainingSeconds"]; ok {
		fmt.Println("冷却剩余秒数:", remaining, "预计重试:", fields["retryAt"])
	}
	return c.log.Encode(fields)
}

func findGame(path string) (uint32, error) {
	s, e := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if e != nil {
		return 0, e
	}
	defer windows.CloseHandle(s)
	p := windows.ProcessEntry32{Size: uint32(unsafe.Sizeof(windows.ProcessEntry32{}))}
	var found uint32
	for e = windows.Process32First(s, &p); e == nil; e = windows.Process32Next(s, &p) {
		if !strings.EqualFold(windows.UTF16ToString(p.ExeFile[:]), filepath.Base(path)) {
			continue
		}
		h, e := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, p.ProcessID)
		if e != nil {
			return 0, e
		}
		buf := make([]uint16, 32768)
		n := uint32(len(buf))
		e = windows.QueryFullProcessImageName(h, 0, &buf[0], &n)
		windows.CloseHandle(h)
		if e != nil {
			return 0, e
		}
		if strings.EqualFold(filepath.Clean(windows.UTF16ToString(buf[:n])), filepath.Clean(path)) {
			if found != 0 {
				return 0, fmt.Errorf("multiple matching clients; refusing ambiguous attach")
			}
			found = p.ProcessID
		}
	}
	if e != windows.ERROR_NO_MORE_FILES {
		return 0, e
	}
	return found, nil
}

func (c *collector) attach() error {
	pid, e := findGame(c.config.Exe)
	if e != nil {
		return e
	}
	pid, e = c.prepareAddons(pid)
	if e != nil {
		return e
	}
	if pid == 0 {
		if e = c.event("START_CLIENT", nil); e != nil {
			return e
		}
		args := c.config.LaunchCommand
		if len(args) == 0 {
			args = []string{c.config.Exe, "-launcherlogin", "-uid", "wow"}
		}
		// Reuse the configured entry command. No token extraction, credential logging or renewal.
		cmd := exec.Command(args[0], args[1:]...)
		cmd.Dir = filepath.Dir(args[0])
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: !strings.EqualFold(filepath.Clean(args[0]), filepath.Clean(c.config.Exe))}
		if e = cmd.Start(); e != nil {
			return fmt.Errorf("launch failed")
		}
		go func() { _ = cmd.Wait() }()
		deadline := time.Now().Add(90 * time.Second)
		for pid == 0 && time.Now().Before(deadline) {
			if e = pause(c.ctx, time.Second); e != nil {
				return e
			}
			pid, e = findGame(c.config.Exe)
			if e != nil {
				return e
			}
		}
		if pid == 0 {
			return fmt.Errorf("client did not start; entry command not retried")
		}
	}
	c.pid = pid
	c.h, e = windows.OpenProcess(windows.PROCESS_QUERY_INFORMATION|windows.PROCESS_VM_READ, false, pid)
	if e != nil {
		return e
	}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		c.hwnd = winutil.FindTopLevelVisibleHWND(pid)
		if c.hwnd != 0 {
			return c.event("ATTACHED", map[string]any{"pid": pid})
		}
		if e = pause(c.ctx, time.Second); e != nil {
			return e
		}
	}
	return fmt.Errorf("game window unavailable")
}

var disabledAddon = regexp.MustCompile(`(?mi)^AuctionSearchExample:[ \t]*disabled[ \t]*\r?$`)

func (c *collector) prepareAddons(pid uint32) (uint32, error) {
	var paths []string
	for _, ch := range c.config.Characters {
		path := filepath.Join(filepath.Dir(filepath.Dir(ch.Source)), "AddOns.txt")
		b, e := os.ReadFile(path)
		if os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return pid, e
		}
		if disabledAddon.Match(b) {
			paths = append(paths, path)
		}
	}
	if len(paths) == 0 {
		return pid, nil
	}
	if pid != 0 {
		if e := c.event("NORMAL_CLOSE_FOR_ADDON_SETUP", nil); e != nil {
			return pid, e
		}
		hwnd := winutil.FindTopLevelVisibleHWND(pid)
		if hwnd == 0 {
			return pid, fmt.Errorf("cannot close client normally for addon setup")
		}
		ok, _, _ := windows.NewLazySystemDLL("user32.dll").NewProc("PostMessageW").Call(hwnd, 0x10, 0, 0) // WM_CLOSE, never terminate process
		if ok == 0 {
			return pid, fmt.Errorf("normal close message failed")
		}
		deadline := time.Now().Add(90 * time.Second)
		for time.Now().Before(deadline) {
			current, e := findGame(c.config.Exe)
			if e != nil {
				return pid, e
			}
			if current == 0 {
				pid = 0
				break
			}
			if current != pid {
				return current, fmt.Errorf("client restarted concurrently; addon configuration unchanged")
			}
			if e = pause(c.ctx, time.Second); e != nil {
				return pid, e
			}
		}
		if pid != 0 {
			return pid, fmt.Errorf("client did not close normally; addon configuration unchanged")
		}
	}
	for i, path := range paths {
		// Read again after the game has flushed its own configuration.
		b, e := os.ReadFile(path)
		if e != nil {
			return 0, e
		}
		backup := filepath.Join(c.config.Output, fmt.Sprintf("AddOns-before-%02d.txt", i+1))
		if e = os.WriteFile(backup, b, 0600); e != nil {
			return 0, e
		}
		updated := disabledAddon.ReplaceAllFunc(b, func(line []byte) []byte { return []byte(strings.Replace(string(line), "disabled", "enabled", 1)) })
		if disabledAddon.Match(updated) {
			return 0, fmt.Errorf("unexpected addon configuration case; unchanged")
		}
		if e = os.WriteFile(path, updated, 0600); e != nil {
			return 0, e
		}
		if e = c.event("ADDON_ENABLED", map[string]any{"path": path, "backup": backup}); e != nil {
			return 0, e
		}
	}
	return 0, nil
}

func (c *collector) native() (selectionSnapshot, error) {
	var s selectionSnapshot
	e := nativeObservation(c.h, c.pid, c.config.Exe, c.character.Name, c.character.Realm, func(r map[string]any) error { s = r["snapshot"].(selectionSnapshot); return nil })
	return s, e
}

func (c *collector) guard() error {
	if c.ctx.Err() != nil {
		return c.ctx.Err()
	}
	u := windows.NewLazySystemDLL("user32.dll")
	var pid uint32
	u.NewProc("GetWindowThreadProcessId").Call(c.hwnd, uintptr(unsafe.Pointer(&pid)))
	if pid != c.pid {
		return fmt.Errorf("window owner changed")
	}
	// Address the game window directly in either position; never change focus.
	for _, key := range []uintptr{0x10, 0x11, 0x12, 0x5b, 0x5c} {
		v, _, _ := u.NewProc("GetAsyncKeyState").Call(key)
		if v&0x8000 != 0 {
			return fmt.Errorf("modifier held; no input sent")
		}
	}
	return nil
}

func (c *collector) tap(key uintptr) error {
	if e := c.guard(); e != nil {
		return e
	}
	u := windows.NewLazySystemDLL("user32.dll")
	post := u.NewProc("PostMessageW")
	scan, _, _ := u.NewProc("MapVirtualKeyW").Call(key, 0)
	flags := uintptr(1) | (scan << 16)
	if key == 0x24 || key == 0x26 || key == 0x28 {
		flags |= 1 << 24
	}
	down, _, _ := post.Call(c.hwnd, 0x100, key, flags)
	time.Sleep(50 * time.Millisecond)
	up, _, _ := post.Call(c.hwnd, 0x101, key, flags|0xc0000000)
	if down == 0 || up == 0 {
		return fmt.Errorf("key message failed; not retried")
	}
	return nil
}

func (c *collector) fresh(ctx context.Context) (addonFrame, error) {
	f, e := c.status.fresh(ctx, c.h)
	if e != nil {
		return f, e
	}
	if f.Values["world"] != "1" || f.Values["character"] != c.character.Name || f.Values["realm"] != c.character.Realm || f.Values["guid"] == "" {
		return f, fmt.Errorf("world/identity changed")
	}
	if f.Values["prepReady"] != "1" || f.Values["combat"] != "0" {
		return f, fmt.Errorf("character unavailable or in combat")
	}
	c.last = f
	return f, nil
}

func (c *collector) wait(label string, seconds int, predicate func(addonFrame) bool) (addonFrame, error) {
	ctx, cancel := context.WithTimeout(c.ctx, time.Duration(seconds)*time.Second)
	defer cancel()
	for {
		f, e := c.fresh(ctx)
		if e != nil {
			return f, fmt.Errorf("%s: %w", label, e)
		}
		if predicate(f) {
			return f, nil
		}
	}
}

var collectCommands = map[string]string{
	"mount":  "/cast [nomounted,nocombat] 雄壮商队雷龙",
	"target": "/targetexact 收集者温塔",
	"scan":   "/as scan",
	"logout": "/logout",
}

var errLateSnapshot = errors.New("late snapshot arrived before rescan")

func (c *collector) command(name string) error {
	expected := c.last
	text, ok := collectCommands[name]
	if !ok {
		return fmt.Errorf("command not allowlisted")
	}
	f, e := c.wait("before command", 5, func(f addonFrame) bool { return true })
	if e != nil {
		return e
	}
	if f.Values["keyboardFocus"] != "0" || f.Values["chatOpen"] != "0" {
		return fmt.Errorf("an edit box is already open; no command sent")
	}
	if name == "logout" && !sameCompletedRun(expected, f) {
		return fmt.Errorf("logout validation withdrawn or run changed")
	}
	if name == "scan" && hasSnapshot(f) {
		return errLateSnapshot
	}
	if e = c.event("COMMAND_"+name, nil); e != nil {
		return e
	}
	if e = c.tap(0x0d); e != nil {
		return e
	}
	if _, e = c.wait("empty chat", 5, func(f addonFrame) bool { return f.Values["chatOpen"] == "1" && f.Values["chatCommand"] == "empty" }); e != nil {
		return e
	}
	post := windows.NewLazySystemDLL("user32.dll").NewProc("PostMessageW")
	for _, ch := range utf16.Encode([]rune(text)) {
		if e = c.guard(); e != nil {
			return e
		}
		r, _, _ := post.Call(c.hwnd, 0x102, uintptr(ch), 1)
		if r == 0 {
			return fmt.Errorf("text message failed; command not committed")
		}
	}
	if f, e = c.wait("exact command", 5, func(f addonFrame) bool { return f.Values["chatOpen"] == "1" && f.Values["chatCommand"] == name }); e != nil {
		return e
	}
	if name == "logout" && !sameCompletedRun(expected, f) {
		return fmt.Errorf("logout validation changed before submit")
	}
	if name == "scan" && hasSnapshot(f) {
		return errLateSnapshot
	}
	// No clipboard or foreground injection; only commit an exact observed command.
	return c.tap(0x0d)
}

func (c *collector) enter() error {
	if e := c.event("WAIT_CHARACTER_OR_WORLD", nil); e != nil {
		return e
	}
	deadline := time.Now().Add(90 * time.Second)
	var selected bool
	for time.Now().Before(deadline) {
		s, e := c.native()
		if e == nil && s.InCharacterSelect {
			matches := 0
			for _, r := range s.Rows {
				if r.Name == c.character.Name && r.RealmName == c.character.Realm {
					matches++
				}
			}
			if matches != 1 {
				return fmt.Errorf("target character missing or ambiguous")
			}
			if e = c.event("SELECT_CHARACTER", nil); e != nil {
				return e
			}
			// Native slots are not UI positions. Observe each navigation result.
			visited := map[string]bool{}
			for steps := 0; steps < 256; steps++ {
				var current *characterRow
				for i := range s.Rows {
					if s.Rows[i].Selected {
						current = &s.Rows[i]
						break
					}
				}
				if current == nil {
					return fmt.Errorf("no selected character")
				}
				if current.Name == c.character.Name && current.RealmName == c.character.Realm {
					selected = true
					break
				}
				if visited[current.GUID] {
					return fmt.Errorf("navigation stopped advancing; no blind retry")
				}
				visited[current.GUID] = true
				key := uintptr(0x28)
				if steps == 0 {
					key = 0x24
				}
				if e = c.tap(key); e != nil {
					return e
				}
				if e = pause(c.ctx, 600*time.Millisecond); e != nil {
					return e
				}
				s, e = c.native()
				if e != nil || !s.InCharacterSelect {
					return fmt.Errorf("selection changed unexpectedly")
				}
				// Home may already be at the first row. Allow one unchanged Home.
				if steps == 0 {
					visited = map[string]bool{}
				}
			}
			if !selected {
				return fmt.Errorf("target not reached within navigation limit")
			}
			if e = c.event("ENTER_WORLD", nil); e != nil {
				return e
			}
			if e = c.tap(0x0d); e != nil {
				return e
			}
			break
		}
		if e == nil && !s.InCharacterSelect {
			ctx, cancel := context.WithTimeout(c.ctx, 3*time.Second)
			b, f, found := locateStatus(ctx, c.h)
			cancel()
			if found == nil {
				if f.Values["character"] != c.character.Name || f.Values["realm"] != c.character.Realm {
					return fmt.Errorf("different character already in world")
				}
				c.status = b
				return nil
			}
		}
		if e = pause(c.ctx, time.Second); e != nil {
			return e
		}
	}
	if !selected {
		return fmt.Errorf("no confirmed character/world state; authentication or loading may need attention")
	}
	if e := pause(c.ctx, 2*time.Second); e != nil {
		return e
	}
	deadline = time.Now().Add(90 * time.Second)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(c.ctx, 30*time.Second)
		b, f, e := locateStatus(ctx, c.h)
		cancel()
		if e == nil {
			if f.Values["character"] != c.character.Name || f.Values["realm"] != c.character.Realm {
				return fmt.Errorf("entered wrong character")
			}
			c.status = b
			return nil
		}
		if logErr := c.event("WAIT_ADDON", map[string]any{"diagnostic": e.Error()}); logErr != nil {
			return logErr
		}
		if e = pause(c.ctx, time.Second); e != nil {
			return e
		}
	}
	return fmt.Errorf("fresh addon heartbeat unavailable after entering world")
}

func (c *collector) prepare() error {
	f, e := c.wait("preparation", 5, func(f addonFrame) bool { return f.Values["casting"] == "0" })
	if e != nil {
		return e
	}
	if f.Values["auctionOpen"] == "1" {
		return nil
	}
	if f.Values["mounted"] == "1" && f.Values["brutosaur"] != "1" {
		return fmt.Errorf("already on a different mount")
	}
	if f.Values["brutosaur"] != "1" {
		if e = c.command("mount"); e != nil {
			return e
		}
		if _, e = c.wait("mount ready", 15, func(f addonFrame) bool {
			return f.Values["brutosaur"] == "1" && f.Values["casting"] == "0" && f.Values["chatOpen"] == "0"
		}); e != nil {
			return e
		}
	}
	if e = c.command("target"); e != nil {
		return e
	}
	if _, e = c.wait("auctioneer target", 8, func(f addonFrame) bool {
		return f.Values["targetAuctioneer"] == "1" && f.Values["chatOpen"] == "0" && f.Values["keyboardFocus"] == "0"
	}); e != nil {
		return e
	}
	if e = c.event("INTERACT_F1", nil); e != nil {
		return e
	}
	if e = c.tap(0x70); e != nil {
		return e
	}
	_, e = c.wait("auction open", 15, func(f addonFrame) bool { return f.Values["auctionOpen"] == "1" })
	return e
}

func validCompleted(f addonFrame) bool {
	v := f.Values
	return v["phase"] == "logout_ready" && v["validated"] == "1" && v["run"] != "" && v["run"] != "none" && v["missingCore"] == "0" && v["incomplete"] == "0" && v["apiErrors"] == "0" && v["unknownScope"] == "0" && v["processed"] == v["total"] && v["total"] != "0" && v["total"] != ""
}

func sameCompletedRun(expected, current addonFrame) bool {
	return validCompleted(current) && expected.Session == current.Session && expected.Values["run"] == current.Values["run"] && expected.Values["guid"] == current.Values["guid"]
}

func (c *collector) round(index int) error {
	started := time.Now()
	if e := c.enter(); e != nil {
		return e
	}
	if c.character.ResumeRun != "" {
		f, e := c.wait("resume identity", 5, func(f addonFrame) bool { return true })
		if e != nil {
			return e
		}
		if f.Session != c.character.ResumeSession || f.Values["run"] != c.character.ResumeRun {
			return fmt.Errorf("resume session/run mismatch")
		}
		stamp, e := strconv.ParseInt(f.Values["scanStartedAt"], 10, 64)
		if e != nil || stamp <= 0 {
			return fmt.Errorf("resume scan time unavailable")
		}
		started = time.Unix(stamp, 0)
		if e = c.event("RESUME_SCAN", map[string]any{"session": f.Session, "run": f.Values["run"]}); e != nil {
			return e
		}
	}
	if c.character.ResumeRun == "" {
		if e := c.prepare(); e != nil {
			return e
		}
	}
	f, e := c.wait("scan status", 5, func(f addonFrame) bool { return true })
	if e != nil {
		return e
	}
	if f.Values["autoCollect"] != "1" {
		return fmt.Errorf("automatic collection disabled for this character")
	}
	if e = c.event("WAIT_SCAN", nil); e != nil {
		return e
	}
	f, e = c.watchScan(f)
	if e != nil {
		return e
	}
	completed := f
	scanTime, parseErr := strconv.ParseInt(f.Values["scanStartedAt"], 10, 64)
	if parseErr != nil || scanTime < started.Unix() {
		return fmt.Errorf("completed snapshot predates this round; no logout or automatic rescan")
	}
	if e = c.event("SCAN_VALIDATED", map[string]any{"session": f.Session, "run": f.Values["run"], "total": f.Values["total"]}); e != nil {
		return e
	}
	if e = c.command("logout"); e != nil {
		return e
	}
	deadline := time.Now().Add(90 * time.Second)
	returned := false
	for time.Now().Before(deadline) {
		s, e := c.native()
		if e == nil && s.InCharacterSelect {
			returned = true
			break
		}
		if e = pause(c.ctx, time.Second); e != nil {
			return e
		}
	}
	if !returned {
		return fmt.Errorf("logout did not reach character selection")
	}
	if e = c.event("WAIT_FILE_FLUSH", nil); e != nil {
		return e
	}
	dir := filepath.Join(c.config.Output, fmt.Sprintf("character-%02d", index+1), "attempt-"+time.Now().Format("20060102-150405.000000000"))
	if e = os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	dest := filepath.Join(dir, "snapshot.lua")
	deadline = time.Now().Add(90 * time.Second)
	for time.Now().Before(deadline) {
		a, e := os.Stat(c.character.Source)
		if e != nil || a.ModTime().Before(started) {
			if e = pause(c.ctx, time.Second); e != nil {
				return e
			}
			continue
		}
		if e = pause(c.ctx, 2*time.Second); e != nil {
			return e
		}
		b, e := os.Stat(c.character.Source)
		if e != nil || a.Size() != b.Size() || !a.ModTime().Equal(b.ModTime()) {
			continue
		}
		report, e := snapshot.SyncAndValidateWithCheck(c.character.Source, dest, started, func(r snapshot.Report) error {
			s := r.Latest
			if s.RunID != completed.Values["run"] || s.CharacterGUID != completed.Values["guid"] || s.CharacterName != c.character.Name || s.RealmName != c.character.Realm || s.MarketScopeUnknownCount == nil || *s.MarketScopeUnknownCount != 0 {
				return fmt.Errorf("saved snapshot identity/run/scope mismatch")
			}
			if fmt.Sprint(s.ActualItemCount) != completed.Values["total"] {
				return fmt.Errorf("saved count differs from validated live count")
			}
			return nil
		})
		if e != nil {
			return e
		}
		archive, e := snapshot.ArchiveValidatedSnapshot(dest, dir, report)
		if e != nil {
			return e
		}
		if c.config.Sync != nil && c.config.Sync.Enabled {
			if queueErr := syncqueue.Enqueue(c.config.Sync.QueueDir, dest, archive.ArchivePath, archive.SnapshotSHA256, archive.ArchiveSHA256, report.Latest.Timestamp); queueErr != nil {
				if e = c.event("SYNC_QUEUE_ERROR", map[string]any{"reason": queueErr.Error()}); e != nil {
					return e
				}
			}
		}
		return c.event("ARCHIVED", map[string]any{"archive": archive.ArchivePath, "sha256": archive.SnapshotSHA256})
	}
	return fmt.Errorf("new SavedVariables file not observed")
}

func runCollection(ctx context.Context, path string) error {
	cfg, e := loadCollectConfig(path)
	if e != nil {
		return e
	}
	mutexName, _ := windows.UTF16PtrFromString("Local\\WowAuctionBackgroundCollector")
	mutex, lockErr := windows.CreateMutex(nil, false, mutexName)
	if mutex != 0 {
		defer windows.CloseHandle(mutex)
	}
	if lockErr != nil {
		return fmt.Errorf("another collector exists or lock unavailable: %w", lockErr)
	}
	if cfg.CycleIntervalSeconds > 0 {
		return runScheduled(ctx, cfg, runSingleCycle)
	}
	return runSingleCycle(ctx, cfg, nil)
}

func runSingleCycle(ctx context.Context, cfg collectConfig, afterCharacter func(int) error) error {
	// Scheduled recovery reuses the cycle directory, never individual attempt files.
	if e := os.Mkdir(cfg.Output, 0700); e != nil && !(os.IsExist(e) && cfg.CycleIntervalSeconds > 0) {
		return fmt.Errorf("output directory must be new: %w", e)
	}
	flags := os.O_CREATE | os.O_WRONLY | os.O_APPEND
	if cfg.CycleIntervalSeconds == 0 {
		flags |= os.O_EXCL
	}
	f, e := os.OpenFile(filepath.Join(cfg.Output, "events.jsonl"), flags, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	c := &collector{ctx: ctx, config: cfg, log: json.NewEncoder(f)}
	defer func() {
		if c.h != 0 {
			windows.CloseHandle(c.h)
		}
	}()
	done := 0
	for i, ch := range cfg.Characters {
		c.character = ch
		archived, verifyErr := hasVerifiedArchive(cfg.Output, i, ch, cfg.CycleStart)
		if verifyErr != nil {
			e = verifyErr
			break
		}
		if archived {
			done++
			if e = c.event("ARCHIVE_REUSED", nil); e != nil {
				break
			}
			if afterCharacter != nil {
				if e = afterCharacter(done); e != nil {
					break
				}
			}
			continue
		}
		if c.h == 0 {
			if e = c.attach(); e != nil {
				break
			}
		}
		c.character = ch
		c.status = nil
		e = c.round(i)
		if e != nil {
			break
		}
		done++
		if afterCharacter != nil {
			if e = afterCharacter(done); e != nil {
				break
			}
		}
	}
	if e != nil {
		_ = c.event("STOPPED", map[string]any{"reason": e.Error()})
		return e
	}
	return c.event("DONE", nil)
}
