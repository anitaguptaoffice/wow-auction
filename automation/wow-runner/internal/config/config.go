package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// DefaultPlaceholderTemplate 为仓库内 tools/genplaceholder 生成的占位 PNG 相对路径（相对配置文件目录）。
// 各顶层模板字段若留空，Load 时会自动填此值，便于先跑通流程再换实机截图。
const DefaultPlaceholderTemplate = "assets/placeholder.png"

// Root is the top-level config loaded from YAML.
type Root struct {
	ConfigDir string `yaml:"-"` // directory of the loaded config file (for relative template paths)

	Display    Display    `yaml:"display"`
	Process    Process    `yaml:"process"`
	Bnet       Bnet       `yaml:"bnet"`
	Keys       Keys       `yaml:"keys"`
	Timeouts   Timeouts   `yaml:"timeouts_seconds"`
	Retry      Retry      `yaml:"retry"`
	Characters Characters `yaml:"characters"`
	Templates  Templates  `yaml:"templates"`
	Vision     Vision     `yaml:"vision"`
	OCR        OCR        `yaml:"ocr"`
	Snapshot   Snapshot   `yaml:"snapshot"`
	Debug      Debug      `yaml:"debug"`
	Logging    Logging    `yaml:"logging"`
}

type Display struct {
	Resolution            string `yaml:"resolution"`
	WindowsScalingPercent int    `yaml:"windows_scaling_percent"`
}

type Process struct {
	BattleNetExe string `yaml:"battle_net_exe"`
	// BattleNetLaunchExe 可选：战网未运行时用于启动的可执行文件绝对路径（如 ...\Battle.net\Battle.net.exe）。空则不自启战网。
	BattleNetLaunchExe string `yaml:"battle_net_launch_exe"`
	WowExe             string `yaml:"wow_exe"`
}

type Bnet struct {
	// GameSelectClick：战网左侧/顶部的「魔兽世界」入口；(0,0) 表示当前已在游戏页。
	GameSelectClick Point `yaml:"game_select_click"`
	EnterGameClick  Point `yaml:"enter_game_click"`
	// OCR 标签用于自动定位游戏入口和「进入游戏」按钮；坐标作为 OCR 失败时的回退。
	GameLabels []string `yaml:"game_labels"`
	PlayLabels []string `yaml:"play_labels"`
	// ReadyTemplate：战网主界面就绪（再点「进入游戏」）；占位 PNG，实机替换。
	ReadyTemplate string `yaml:"ready_template"`
	SearchROI     *ROI   `yaml:"search_roi"`
}

type Point struct {
	X int `yaml:"x"`
	Y int `yaml:"y"`
}

type Keys struct {
	AuctionTarMacro  string `yaml:"auction_tar_macro"`
	AuctioneerTarget string `yaml:"auctioneer_target"`
	InteractTarget   string `yaml:"interact_target"`
	LogoutMacro      string `yaml:"logout_macro"`
	CharHome         string `yaml:"char_home"`
	CharSelectDown   string `yaml:"char_select_down"`
	EnterWorld       string `yaml:"enter_world"`
}

type Timeouts struct {
	BnetStart int `yaml:"bnet_start"`
	// BnetUIReady：等待战网就绪模板的最长时间（秒）。
	BnetUIReady   int `yaml:"bnet_ui_ready"`
	WowForeground int `yaml:"wow_foreground"`
	CharSelect    int `yaml:"char_select"`
	EnterWorld    int `yaml:"enter_world"`
	// EnterWorldLoad is sleep after pressing enter (no template yet); 0 = runner default (e.g. 5s).
	EnterWorldLoad      int `yaml:"enter_world_load"`
	AHOpen              int `yaml:"ah_open"`
	AHPrep              int `yaml:"ah_prep"`
	MaxSinceScanTrigger int `yaml:"max_since_scan_trigger"`
	// GracefulExit：/logout 或 /quit 后等待选角界面/进程自然退出的最长时间。
	GracefulExit int `yaml:"graceful_exit"`
}

type Retry struct {
	MaxRetriesPerCharacter int `yaml:"max_retries_per_character"`
	MaxRestartTotal        int `yaml:"max_restart_total"`
}

type Characters struct {
	Mode    string `yaml:"mode"`
	Indices []int  `yaml:"indices"`
}

type Templates struct {
	AHOpenOK            string `yaml:"ah_open_ok"`
	PluginScanStarted   string `yaml:"plugin_scan_started"`
	PluginScanComplete  string `yaml:"plugin_scan_complete"`
	CharSelectScreen    string `yaml:"char_select_screen"`
	EnterWorldActionbar string `yaml:"enter_world_actionbar"`
}

// Vision 控制模板轮询与 ROI；阈值为 0 时 runner 使用默认值。
type Vision struct {
	PollIntervalMS int     `yaml:"poll_interval_ms"`
	MatchThreshold float64 `yaml:"match_threshold"`
	SearchROI      *ROI    `yaml:"search_roi"`
	// MatchMethod：ncc（默认，等价 OpenCV TM_CCOEFF_NORMED 映射到 [0,1]）或 rgb_mean（旧实现）。
	MatchMethod string `yaml:"match_method"`
	// ColorGateMaxAvgChannelDiff：>0 时在候选位置校验 RGB 平均绝对差（每通道 0–255 再对三通道取平均），超过则否决该位置。
	ColorGateMaxAvgChannelDiff float64 `yaml:"color_gate_max_avg_channel_diff"`
}

// OCR 使用 Windows.Media.Ocr 识别插件状态面板。模板匹配仍用于选角、动作条和拍卖行门禁。
type OCR struct {
	Enabled          bool     `yaml:"enabled"`
	Language         string   `yaml:"language"`
	SearchROI        *ROI     `yaml:"search_roi"`
	PollIntervalMS   int      `yaml:"poll_interval_ms"`
	StableReads      int      `yaml:"stable_reads"`
	WaitingTokens    []string `yaml:"waiting_tokens"`
	ScanningTokens   []string `yaml:"scanning_tokens"`
	CompleteTokens   []string `yaml:"complete_tokens"`
	WarningTokens    []string `yaml:"warning_tokens"`
	ErrorTokens      []string `yaml:"error_tokens"`
	ReadyTokens      []string `yaml:"ready_tokens"`
	CharSelectTokens []string `yaml:"char_select_tokens"`
}

// Snapshot 控制正常退出后 SavedVariables 的发现、验证与原子同步。
type Snapshot struct {
	// Source 可直接指定 .../SavedVariables/AuctionSearchExample.lua；非空时优先。
	Source string `yaml:"source"`
	// RetailRoot 例如 F:\\World of Warcraft；Source 为空时从该目录下发现账号文件。
	RetailRoot string `yaml:"retail_root"`
	// Account 是 WTF/Account 下的目录名或其唯一子串，用于避免多账号串档。
	Account string `yaml:"account"`
	// Destination 通常为仓库 data/auction.lua，可相对配置文件目录。
	Destination string `yaml:"destination"`
	// ArchiveDir 保存每次验证成功的 gzip 压缩归档和清单。
	ArchiveDir string `yaml:"archive_dir"`
	// ImportEnabled 启用网站数据库追加导入。
	ImportEnabled bool `yaml:"import_enabled"`
	// PythonExe 与 ImporterScript 指向后端导入器运行时和入口。
	PythonExe      string `yaml:"python_exe"`
	ImporterScript string `yaml:"importer_script"`
	// DatabaseURL 为空时使用后端默认 data/wow-auction.db；非空时仅通过子进程环境传递。
	DatabaseURL string `yaml:"database_url"`
	// ClearSourceAfterImport 仅在归档、导入和复核全部成功后原子清空 WoW 源文件。
	ClearSourceAfterImport bool `yaml:"clear_source_after_import"`
}

// Debug 调试输出。
type Debug struct {
	// FailureCaptureDir：非空时失败将当前窗口客户区截图写入该目录（相对配置文件目录）。
	FailureCaptureDir string `yaml:"failure_capture_dir"`
}

// ROI 为相对客户区左上角的像素矩形；若 w/h 为 0 表示使用整屏客户区。
type ROI struct {
	X int `yaml:"x"`
	Y int `yaml:"y"`
	W int `yaml:"w"`
	H int `yaml:"h"`
}

type Logging struct {
	Level string `yaml:"level"`
}

// Load reads and parses a YAML config file.
func Load(path string) (*Root, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("config path: %w", err)
	}
	b, err := os.ReadFile(abs)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var root Root
	if err := yaml.Unmarshal(b, &root); err != nil {
		return nil, fmt.Errorf("parse yaml: %w", err)
	}
	root.ConfigDir = filepath.Dir(abs)
	root.applyDefaultTemplatePaths()
	root.applyOCRDefaults()
	root.applyBnetDefaults()
	if err := root.Validate(); err != nil {
		return nil, err
	}
	return &root, nil
}

// applyDefaultTemplatePaths fills empty top-level template paths with DefaultPlaceholderTemplate.
// LogoutStep.template 若为空白则保留（表示仅点击、不等待模板）。
func (r *Root) applyDefaultTemplatePaths() {
	set := func(s *string) {
		if strings.TrimSpace(*s) == "" {
			*s = DefaultPlaceholderTemplate
		}
	}
	t := &r.Templates
	set(&t.AHOpenOK)
	set(&t.CharSelectScreen)
	set(&t.EnterWorldActionbar)
}

func (r *Root) applyOCRDefaults() {
	o := &r.OCR
	if strings.TrimSpace(o.Language) == "" {
		o.Language = "zh-Hans-CN"
	}
	if o.PollIntervalMS <= 0 {
		o.PollIntervalMS = 750
	}
	if o.StableReads <= 0 {
		o.StableReads = 2
	}
	if len(o.WaitingTokens) == 0 {
		o.WaitingTokens = []string{"AS_WAITING"}
	}
	if len(o.ScanningTokens) == 0 {
		o.ScanningTokens = []string{"AS_SCANNING"}
	}
	if len(o.CompleteTokens) == 0 {
		o.CompleteTokens = []string{"AS_COMPLETE"}
	}
	if len(o.WarningTokens) == 0 {
		o.WarningTokens = []string{"AS_WARNING"}
	}
	if len(o.ErrorTokens) == 0 {
		o.ErrorTokens = []string{"AS_ERROR"}
	}
	if len(o.ReadyTokens) == 0 {
		o.ReadyTokens = []string{"AS_READY"}
	}
	if len(o.CharSelectTokens) == 0 {
		o.CharSelectTokens = []string{"进入魔兽世界", "Enter World"}
	}
}

func (r *Root) applyBnetDefaults() {
	if len(r.Bnet.GameLabels) == 0 {
		r.Bnet.GameLabels = []string{"魔兽世界", "World of Warcraft"}
	}
	if len(r.Bnet.PlayLabels) == 0 {
		r.Bnet.PlayLabels = []string{"进入游戏", "Play"}
	}
	if strings.TrimSpace(r.Snapshot.Destination) == "" {
		r.Snapshot.Destination = "../../data/auction.lua"
	}
	if strings.TrimSpace(r.Snapshot.ArchiveDir) == "" {
		r.Snapshot.ArchiveDir = "../../data/archive"
	}
}

// ResolvePath joins p with the config file directory when p is not absolute.
func (r *Root) ResolvePath(p string) string {
	if p == "" {
		return ""
	}
	if filepath.IsAbs(p) {
		return filepath.Clean(p)
	}
	return filepath.Join(r.ConfigDir, filepath.Clean(p))
}

// ResolveSnapshotPath 与 ResolvePath 相同，但保留空值，便于自动发现源文件。
func (r *Root) ResolveSnapshotPath(p string) string {
	return r.ResolvePath(strings.TrimSpace(p))
}

// Validate checks required fields for a minimal runnable config.
func (r *Root) Validate() error {
	if r.Keys.InteractTarget == "" {
		return fmt.Errorf("keys.interact_target is required")
	}
	if r.Process.WowExe == "" {
		return fmt.Errorf("process.wow_exe is required")
	}
	if r.Process.BattleNetExe == "" {
		return fmt.Errorf("process.battle_net_exe is required")
	}
	if r.Characters.Mode != "all" && r.Characters.Mode != "single" && r.Characters.Mode != "current" {
		return fmt.Errorf("characters.mode must be all, single, or current")
	}
	if len(r.Characters.Indices) == 0 {
		return fmt.Errorf("characters.indices must be non-empty")
	}
	if r.Snapshot.ClearSourceAfterImport && !r.Snapshot.ImportEnabled {
		return fmt.Errorf("snapshot.clear_source_after_import requires snapshot.import_enabled")
	}
	if r.Snapshot.ImportEnabled {
		if strings.TrimSpace(r.Snapshot.PythonExe) == "" {
			return fmt.Errorf("snapshot.python_exe is required when import_enabled is true")
		}
		if strings.TrimSpace(r.Snapshot.ImporterScript) == "" {
			return fmt.Errorf("snapshot.importer_script is required when import_enabled is true")
		}
	}
	return nil
}
