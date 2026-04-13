#!/usr/bin/env bash
# 魔兽世界 macOS 端：启动应用（可选）+ 延时 + 按键序列（辅助功能自动化）
# 用法：cd automation && cp config.env.example config.env && 编辑 config.env 与 key-sequence.txt
#       ./run_macos.sh          执行
#       ./run_macos.sh --dry-run 只打印计划步骤

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="${CONFIG_FILE:-$SCRIPT_DIR/config.env}"
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
	DRY_RUN=1
fi

if [[ ! -f "$CONFIG" ]]; then
	echo "缺少 $CONFIG。请: cp config.env.example config.env 并编辑。" >&2
	exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG"

: "${WOW_APP_NAME:=World of Warcraft}"
: "${BATTLE_NET_APP_NAME:=Battle.net}"
: "${LAUNCH_BATTLE_NET:=0}"
: "${LAUNCH_WOW:=0}"
: "${DELAY_AFTER_LAUNCH:=20}"
: "${DELAY_BEFORE_SEQUENCE:=5}"
: "${KEY_SEQUENCE_FILE:=key-sequence.txt}"
: "${DELAY_AFTER_SCAN:=120}"
: "${RUN_EXIT_SEQUENCE:=0}"
: "${EXIT_SEQUENCE_FILE:=key-sequence-exit.txt}"

log() { echo "[wow-ah-auto] $*"; }

run_delay() {
	local n="$1"
	if [[ "$DRY_RUN" -eq 1 ]]; then log "DELAY ${n}s (dry-run)"; return; fi
	sleep "$n"
}

activate_wow() {
	if [[ "$DRY_RUN" -eq 1 ]]; then log "ACTIVATE $WOW_APP_NAME (dry-run)"; return; fi
	osascript -e "tell application \"$WOW_APP_NAME\" to activate" || true
}

send_keystroke() {
	local text="$1"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "KEYSTROKE -> ${text:0:80}$([[ ${#text} -gt 80 ]] && echo ...)"
		return
	fi
	# 转义双引号与反斜杠供 AppleScript 使用
	local escaped="${text//\\/\\\\}"
	escaped="${escaped//\"/\\\"}"
	osascript -e "tell application \"System Events\" to tell process \"$WOW_APP_NAME\" to keystroke \"$escaped\""
}

send_key_code() {
	local code="$1"
	if [[ "$DRY_RUN" -eq 1 ]]; then log "KEY CODE $code (dry-run)"; return; fi
	osascript -e "tell application \"System Events\" to tell process \"$WOW_APP_NAME\" to key code $code"
}

run_sequence_file() {
	local file="$1"
	if [[ ! -f "$file" ]]; then
		echo "缺少序列文件: $file" >&2
		exit 1
	fi
	while IFS= read -r line || [[ -n "$line" ]]; do
		[[ "$line" =~ ^[[:space:]]*# ]] && continue
		[[ -z "${line// }" ]] && continue
		read -r -a parts <<<"$line"
		local cmd="${parts[0]}"
		case "$cmd" in
		DELAY)
			run_delay "${parts[1]:?DELAY 需要秒数}"
			;;
		ACTIVATE)
			activate_wow
			;;
		KEYSTROKE)
			# 剩余整行作为要输入的文本
			local prefix="KEYSTROKE "
			local rest="${line#"$prefix"}"
			send_keystroke "$rest"
			;;
		ENTER)
			send_key_code 36
			;;
		ESC)
			send_key_code 53
			;;
		*)
			echo "未知指令: $line" >&2
			exit 1
			;;
		esac
	done <"$file"
}

if [[ "$LAUNCH_BATTLE_NET" == "1" ]]; then
	log "启动 Battle.net..."
	if [[ "$DRY_RUN" -eq 0 ]]; then
		open -a "$BATTLE_NET_APP_NAME" || true
	fi
	run_delay 5
fi

if [[ "$LAUNCH_WOW" == "1" ]]; then
	log "启动 $WOW_APP_NAME..."
	if [[ "$DRY_RUN" -eq 0 ]]; then
		open -a "$WOW_APP_NAME" || true
	fi
fi

log "等待 ${DELAY_AFTER_LAUNCH}s（登录/选角/进世界请在此期间完成）..."
run_delay "$DELAY_AFTER_LAUNCH"

log "等待 ${DELAY_BEFORE_SEQUENCE}s 后执行按键序列..."
run_delay "$DELAY_BEFORE_SEQUENCE"

SEQ_PATH="$SCRIPT_DIR/$KEY_SEQUENCE_FILE"
log "执行序列: $SEQ_PATH"
run_sequence_file "$SEQ_PATH"

log "等待扫描 ${DELAY_AFTER_SCAN}s（插件 replicate；可按需调大）..."
run_delay "$DELAY_AFTER_SCAN"

if [[ "$RUN_EXIT_SEQUENCE" == "1" ]]; then
	EX_PATH="$SCRIPT_DIR/$EXIT_SEQUENCE_FILE"
	log "执行退出序列: $EX_PATH"
	run_sequence_file "$EX_PATH"
else
	log "未配置 RUN_EXIT_SEQUENCE；请手动退出游戏以写入 SavedVariables，或启用退出序列。"
fi

log "结束。"
