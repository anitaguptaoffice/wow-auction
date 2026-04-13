package config

import "testing"

func TestApplyDefaultTemplatePaths(t *testing.T) {
	r := &Root{}
	r.applyDefaultTemplatePaths()
	if r.Templates.AHOpenOK != DefaultPlaceholderTemplate {
		t.Fatalf("AHOpenOK: got %q", r.Templates.AHOpenOK)
	}
	if r.Templates.CharSelectScreen != DefaultPlaceholderTemplate {
		t.Fatalf("CharSelectScreen: got %q", r.Templates.CharSelectScreen)
	}
	if r.Templates.EnterWorldActionbar != DefaultPlaceholderTemplate {
		t.Fatalf("EnterWorldActionbar: got %q", r.Templates.EnterWorldActionbar)
	}
}

func TestApplyDefaultTemplatePaths_preservesExplicit(t *testing.T) {
	r := &Root{Templates: Templates{AHOpenOK: "custom/only.png"}}
	r.applyDefaultTemplatePaths()
	if r.Templates.AHOpenOK != "custom/only.png" {
		t.Fatal(r.Templates.AHOpenOK)
	}
	if r.Templates.CharSelectScreen != DefaultPlaceholderTemplate {
		t.Fatal("expected other fields still defaulted")
	}
}
