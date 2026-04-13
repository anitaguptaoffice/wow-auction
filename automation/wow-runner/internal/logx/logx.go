// Package logx writes NDJSON lines to stderr per ../LOGGING_SPEC.md.
package logx

import (
	"encoding/json"
	"io"
	"os"
	"time"
)

// Logger emits structured events with a fixed run_id and component.
type Logger struct {
	w         io.Writer
	runID     string
	version   string
	component string
}

// New returns a logger writing to w (typically os.Stderr).
func New(w io.Writer, runID, version string) *Logger {
	if w == nil {
		w = os.Stderr
	}
	return &Logger{w: w, runID: runID, version: version, component: "wow-runner"}
}

// Emit writes one JSON object with RFC3339Nano ts, level, event, run_id, component, version.
func (l *Logger) Emit(level, event, msg string, fields map[string]any) {
	m := map[string]any{
		"ts":        time.Now().Format(time.RFC3339Nano),
		"level":     level,
		"event":     event,
		"run_id":    l.runID,
		"component": l.component,
		"version":   l.version,
		"msg":       msg,
	}
	for k, v := range fields {
		m[k] = v
	}
	b, _ := json.Marshal(m)
	_, _ = l.w.Write(append(b, '\n'))
}
