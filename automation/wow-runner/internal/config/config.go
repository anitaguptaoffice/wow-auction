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
	EnterGameClick Point `yaml:"enter_game_click"`
	// ReadyTemplate：战网主界面就绪（再点「进入游戏」）；占位 PNG，实机替换。
	ReadyTemplate string `yaml:"ready_template"`
	SearchROI     *ROI   `yaml:"search_roi"`
}

type Point struct {
	X int `yaml:"x"`
	Y int `yaml:"y"`
}

type Keys struct {
	AuctionTarMacro string `yaml:"auction_tar_macro"`
	InteractTarget  string `yaml:"interact_target"`
	CharHome        string `yaml:"char_home"`
	CharSelectDown  string `yaml:"char_select_down"`
	EnterWorld      string `yaml:"enter_world"`
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
}

type Retry struct {
	MaxRetriesPerCharacter int `yaml:"max_retries_per_character"`
	MaxKillRestartTotal    int `yaml:"max_kill_restart_total"`
}

type Characters struct {
	Mode    string `yaml:"mode"`
	Indices []int  `yaml:"indices"`
}

type Templates struct {
	AHOpenOK            string       `yaml:"ah_open_ok"`
	PluginScanStarted   string       `yaml:"plugin_scan_started"`
	PluginScanComplete  string       `yaml:"plugin_scan_complete"`
	CharSelectScreen    string       `yaml:"char_select_screen"`
	EnterWorldActionbar string       `yaml:"enter_world_actionbar"`
	LogoutUISteps       []LogoutStep `yaml:"logout_ui_steps"`
}

// LogoutStep: 先等到 template 在 ROI 内匹配（若 template 非空），再可选点击（客户端坐标相对窗口客户区原点）。
type LogoutStep struct {
	Template string `yaml:"template"`
	Click    *Point `yaml:"click"`
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
	set(&t.PluginScanStarted)
	set(&t.PluginScanComplete)
	set(&t.CharSelectScreen)
	set(&t.EnterWorldActionbar)
	b := &r.Bnet
	set(&b.ReadyTemplate)
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

// Validate checks required fields for a minimal runnable config.
func (r *Root) Validate() error {
	if r.Keys.AuctionTarMacro == "" {
		return fmt.Errorf("keys.auction_tar_macro is required")
	}
	if r.Keys.InteractTarget == "" {
		return fmt.Errorf("keys.interact_target is required")
	}
	if r.Process.WowExe == "" {
		return fmt.Errorf("process.wow_exe is required")
	}
	if r.Process.BattleNetExe == "" {
		return fmt.Errorf("process.battle_net_exe is required")
	}
	if r.Characters.Mode != "all" && r.Characters.Mode != "single" {
		return fmt.Errorf("characters.mode must be all or single")
	}
	if len(r.Characters.Indices) == 0 {
		return fmt.Errorf("characters.indices must be non-empty")
	}
	return nil
}
