// Command wow-runner — Windows 外置自动化主控（战网 → 魔兽 → 多角色拍卖扫描）。
// 行为约定见 ../../DEVELOPMENT_PLAN.md。FSM/视觉持续补充中。
package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/runner"
)

func main() {
	configPath := flag.String("config", "config.yaml", "path to YAML config")
	showVersion := flag.Bool("version", false, "print version and exit")
	checkOnly := flag.Bool("check", false, "load config, poll Battle.net and Wow processes, emit logs, exit")
	runFSM := flag.Bool("run", false, "Windows: run full FSM (multi-char, ROUND_DONE kill, retry on PLUGIN_STUCK; needs Wow or bnet+enter_game_click)")
	flag.Parse()

	if *showVersion {
		fmt.Println("wow-runner dev")
		os.Exit(0)
	}

	root, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "config error: %v\n", err)
		os.Exit(1)
	}

	runID := fmt.Sprintf("run-%d", time.Now().UnixNano())
	log := logx.New(os.Stderr, runID, "dev")

	log.Emit("INFO", "session_start", "wow-runner starting", map[string]any{
		"config_path":     *configPath,
		"char_total":      len(root.Characters.Indices),
		"characters_mode": root.Characters.Mode,
	})

	if *checkOnly {
		runCheck(log, root)
		log.Emit("INFO", "session_end", "check done", map[string]any{
			"exit_code": 0,
			"outcome":   "success",
		})
		os.Exit(0)
	}

	if *runFSM {
		if err := runner.RunFSM(log, root); err != nil {
			log.Emit("ERROR", "session_end", err.Error(), map[string]any{
				"exit_code": 1,
				"outcome":   "failed",
			})
			os.Exit(1)
		}
		log.Emit("INFO", "session_end", "run step finished", map[string]any{
			"exit_code": 0,
			"outcome":   "success",
		})
		os.Exit(0)
	}

	log.Emit("INFO", "session_end", "no action (use -check or -run)", map[string]any{
		"exit_code": 0,
		"outcome":   "success",
	})
}

func runCheck(log *logx.Logger, root *config.Root) {
	names := []struct {
		key  string
		name string
	}{
		{"battle_net", root.Process.BattleNetExe},
		{"wow", root.Process.WowExe},
	}
	for _, n := range names {
		pids, err := proc.PIDsByExe(n.name)
		fields := map[string]any{
			"name": n.name,
		}
		if err != nil {
			fields["found"] = false
			fields["error"] = err.Error()
			log.Emit("WARN", "process_poll", "process list failed", fields)
			continue
		}
		fields["found"] = len(pids) > 0
		if len(pids) == 1 {
			fields["pid"] = pids[0]
		} else if len(pids) > 1 {
			fields["pids"] = pids
		}
		log.Emit("INFO", "process_poll", "process query", fields)
	}
}
