//go:build windows

// key-probe only sends one key pair after an explicit local-page request.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"sync"
	"time"

	"golang.org/x/sys/windows"
	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/winutil"
)

var user32 = windows.NewLazySystemDLL("user32.dll")
var postMessage = user32.NewProc("PostMessageW")
var mapKey = user32.NewProc("MapVirtualKeyW")
var foreground = user32.NewProc("GetForegroundWindow")

type target struct {
	PID  int32   `json:"pid"`
	HWND uintptr `json:"hwnd"`
}

func findTarget() (target, error) {
	pids, err := proc.PIDsByExe("Wow.exe")
	if err != nil {
		return target{}, err
	}
	if len(pids) != 1 {
		return target{}, fmt.Errorf("需要恰好一个 Wow.exe，目前 %d 个", len(pids))
	}
	hwnd := winutil.FindTopLevelVisibleHWND(uint32(pids[0]))
	if hwnd == 0 {
		return target{}, fmt.Errorf("未找到游戏窗口")
	}
	return target{pids[0], hwnd}, nil
}

func main() {
	t, err := findTarget()
	if err != nil {
		log.Fatal(err)
	}
	if len(os.Args) > 1 && os.Args[1] == "--check" {
		json.NewEncoder(os.Stdout).Encode(t)
		return
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatal(err)
	}
	secret := make([]byte, 24)
	if _, err := rand.Read(secret); err != nil {
		log.Fatal(err)
	}
	path := "/" + hex.EncodeToString(secret)
	origin := "http://" + listener.Addr().String()
	var mutex sync.Mutex
	var last time.Time
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
		if r.Host != listener.Addr().String() || r.URL.Path != path {
			http.NotFound(w, r)
			return
		}
		if r.Method == "GET" {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			fmt.Fprintf(w, page, t.PID, t.HWND)
			return
		}
		if r.Method != "POST" || r.Header.Get("Origin") != origin {
			http.Error(w, "请求来源不允许", 403)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 64)
		var body struct {
			Key string `json:"key"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil || (body.Key != "1" && body.Key != "F1") {
			http.Error(w, "只支持数字 1 或 F1", 400)
			return
		}
		mutex.Lock()
		defer mutex.Unlock()
		if time.Since(last) < time.Second {
			http.Error(w, "请勿连续触发", 429)
			return
		}
		current, err := findTarget()
		if err != nil || current != t {
			http.Error(w, "游戏进程或窗口变化，请重启测试器", 409)
			return
		}
		vk := uintptr(0x31)
		if body.Key == "F1" {
			vk = 0x70
		}
		scan, _, _ := mapKey.Call(vk, 0)
		before, _, _ := foreground.Call()
		flags := uintptr(1) | scan<<16
		// Always post the release too; never focus, inject globally, or retry.
		down, _, downErr := postMessage.Call(t.HWND, 0x100, vk, flags)
		up, _, upErr := postMessage.Call(t.HWND, 0x101, vk, flags|0xC0000000)
		after, _, _ := foreground.Call()
		last = time.Now()
		result := map[string]any{"time": last.Format(time.RFC3339Nano), "key": body.Key, "pid": t.PID, "down_queued": down != 0, "up_queued": up != 0, "foreground_before": before, "foreground_after": after, "game_was_foreground": before == t.HWND, "game_received": "unconfirmed"}
		if down == 0 {
			result["down_error"] = downErr.Error()
		}
		if up == 0 {
			result["up_error"] = upErr.Error()
		}
		json.NewEncoder(os.Stdout).Encode(result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})
	json.NewEncoder(os.Stdout).Encode(map[string]any{"url": origin + path, "target": t, "keys_sent": 0})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second}
	log.Fatal(server.Serve(listener))
}

const page = `<!doctype html><meta charset="utf-8"><title>Go 后台按键测试</title>
<style>body{font:18px system-ui;background:#111923;color:#e9edf2;max-width:850px;margin:40px auto;padding:20px}button{padding:14px;margin:8px 8px 8px 0;font-size:18px}textarea{width:95%%;padding:12px;font-size:20px}pre{white-space:pre-wrap;font-size:14px;background:#223040;padding:15px}#status{min-height:55px;color:#9ce7c9}</style>
<h1>Go · 后台按键单次测试</h1><p>目标 PID %d · 窗口 %d。1＝选中温塔，F1＝交互。</p>
<p>每次点按钮后等待 5 秒。不会切前台，不会自动扫描、循环或重试。</p>
<button class="send" onclick="schedule('1')">发送 1 · 选中目标</button><button class="send" onclick="schedule('F1')">发送 F1 · 交互</button><button onclick="cancel()">取消倒计时</button>
<p id="status">尚未发送按键。保持魔兽聊天输入框关闭。</p>
<p>测试时在这里持续打字，观察焦点是否改变：</p><textarea rows="4" placeholder="在这里打字"></textarea><h3>结果（已入队不代表游戏执行成功）</h3><pre id="log"></pre>
<script>
let timer=null,busy=false;
const status=document.getElementById('status'),output=document.getElementById('log');
function buttons(off){document.querySelectorAll('.send').forEach(b=>b.disabled=off)}
function cancel(){if(busy)return;if(timer!==null)clearTimeout(timer);timer=null;buttons(false);status.textContent='已取消待发送消息。'}
function schedule(key){if(busy||timer!==null)return;buttons(true);let n=5;const tick=async()=>{timer=null;if(n>0){status.textContent=n+' 秒后向魔兽发送数字 '+key;n--;timer=setTimeout(tick,1000);return}busy=true;status.textContent='正在提交一次消息…';try{const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});const text=await r.text();output.textContent=text+'\n'+output.textContent;status.textContent=r.ok?'消息处理结束，请观察游戏是否响应。':'提交失败，没有自动重试。'}catch(e){status.textContent='请求结果未知，请先观察游戏，勿直接重试。'}finally{busy=false;buttons(false)}};tick()}
window.addEventListener('pagehide',()=>{if(timer!==null)clearTimeout(timer)});
</script>`
