package syncapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testID = "0123456789abcdef0123456789abcdef"

func TestCreateUploadAndStatus(t *testing.T) {
	data := []byte("compressed fixture")
	h := sha256.Sum256(data)
	m := Manifest{SchemaVersion: 1, CompressedSHA256: hex.EncodeToString(h[:]), SizeBytes: int64(len(data)), RegionID: 5, RealmID: 887, CapturedAt: 1700000000, RecordCount: 1}
	base := "/api/sync/jobs/" + testID
	var methods []string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("missing bearer")
		}
		methods = append(methods, r.Method)
		if r.Method == http.MethodPut {
			b, _ := io.ReadAll(r.Body)
			if string(b) != string(data) || r.Header.Get("Content-Type") != "application/gzip" || r.ContentLength != int64(len(data)) {
				t.Error("upload mismatch")
			}
		}
		phase := "awaiting_upload"
		if r.Method == http.MethodPut {
			phase = "queued"
		}
		if r.Method == http.MethodGet {
			phase = "succeeded"
		}
		_ = json.NewEncoder(w).Encode(Job{ID: testID, Phase: phase, StatusURL: base, UploadURL: base + "/snapshot"})
	}))
	defer srv.Close()
	c, e := New(srv.URL, "test-token", srv.Client().Transport)
	if e != nil {
		t.Fatal(e)
	}
	ctx := context.Background()
	j, e := c.Create(ctx, m)
	if e != nil || j.Phase != "awaiting_upload" {
		t.Fatalf("create: %+v %v", j, e)
	}
	p := filepath.Join(t.TempDir(), "snapshot.jsonl.gz")
	if e = os.WriteFile(p, data, 0600); e != nil {
		t.Fatal(e)
	}
	j, e = c.Upload(ctx, testID, p, m)
	if e != nil || j.Phase != "queued" {
		t.Fatalf("upload: %+v %v", j, e)
	}
	j, e = c.Status(ctx, testID)
	if e != nil || j.Phase != "succeeded" {
		t.Fatalf("status: %+v %v", j, e)
	}
	if strings.Join(methods, ",") != "POST,PUT,GET" {
		t.Fatal(methods)
	}
	if e = os.WriteFile(p, []byte("other content"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = c.Upload(ctx, testID, p, m); e == nil {
		t.Fatal("changed file uploaded")
	}
	if len(methods) != 3 {
		t.Fatal("invalid file reached server")
	}
}

func TestBusyAuthAndUnsafeResponse(t *testing.T) {
	mode := "busy"
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch mode {
		case "busy":
			w.Header().Set("Retry-After", "17")
			w.WriteHeader(409)
		case "auth":
			w.WriteHeader(401)
		case "redirect":
			w.Header().Set("Location", "https://example.com/")
			w.WriteHeader(302)
		case "bad":
			_ = json.NewEncoder(w).Encode(Job{ID: testID, StatusURL: "https://example.com", UploadURL: "/x"})
		}
	}))
	defer srv.Close()
	c, e := New(srv.URL, "test-token", srv.Client().Transport)
	if e != nil {
		t.Fatal(e)
	}
	_, e = c.Status(context.Background(), testID)
	var busy *BusyError
	if !errors.As(e, &busy) || busy.RetryAfter != 17*time.Second {
		t.Fatalf("busy: %v", e)
	}
	mode = "auth"
	if _, e = c.Status(context.Background(), testID); !errors.Is(e, ErrAuthentication) {
		t.Fatal(e)
	}
	mode = "redirect"
	if _, e = c.Status(context.Background(), testID); e == nil {
		t.Fatal("followed redirect")
	}
	mode = "bad"
	if _, e = c.Status(context.Background(), testID); e == nil {
		t.Fatal("accepted untrusted job URL")
	}
	if _, e = New("http://example.com", "token", nil); e == nil {
		t.Fatal("accepted non-HTTPS")
	}
}
