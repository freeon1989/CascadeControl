package xraypatch

import (
	"os"
	"path/filepath"
	"testing"
)

func TestApplyRewritesOnlySelector(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	original := []byte(`{
	  "routing": {
	    "balancers": [
	      {"tag": "cascade", "selector": ["exit-old"]}
	    ]
	  },
	  "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {"secret": "keep-me"}}]
	}`)
	if err := os.WriteFile(path, original, 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err := Apply(path, "cascade", []string{"nl", "de"})
	if err != nil || !changed {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	again, err := Apply(path, "cascade", []string{"nl", "de"})
	if err != nil || again {
		t.Fatalf("повтор не должен переписывать файл: %v %v", again, err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	if !contains(text, "exit-nl") || !contains(text, "keep-me") {
		t.Fatalf("конфиг повреждён: %s", text)
	}
}

func contains(text, part string) bool {
	return len(text) >= len(part) && (text == part || len(part) == 0 || indexOf(text, part) >= 0)
}

func indexOf(text, part string) int {
	for i := 0; i+len(part) <= len(text); i++ {
		if text[i:i+len(part)] == part {
			return i
		}
	}
	return -1
}
