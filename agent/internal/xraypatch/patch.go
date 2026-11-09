// Точечная правка селектора балансировщика. Остальной JSON, включая ключи, не логируется.
package xraypatch

import (
	"encoding/json"
	"os"
	"path/filepath"
)

func Apply(path string, balancerTag string, exitIDs []string) (bool, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return false, err
	}
	routing, _ := doc["routing"].(map[string]any)
	balancers, _ := routing["balancers"].([]any)
	selector := make([]any, 0, len(exitIDs))
	for _, id := range exitIDs {
		selector = append(selector, "exit-"+id)
	}
	changed := false
	for _, item := range balancers {
		balancer, _ := item.(map[string]any)
		if balancer["tag"] != balancerTag {
			continue
		}
		current, _ := balancer["selector"].([]any)
		if same(current, selector) {
			continue
		}
		balancer["selector"] = selector
		changed = true
	}
	if !changed {
		return false, nil
	}
	encoded, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return false, err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, encoded, 0o600); err != nil {
		return false, err
	}
	if err := os.Rename(tmp, path); err != nil {
		return false, err
	}
	return true, nil
}

func same(left, right []any) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func EnsureDir(path string) error {
	return os.MkdirAll(filepath.Dir(path), 0o755)
}
