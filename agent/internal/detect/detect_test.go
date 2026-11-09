package detect

import "testing"

func TestClassify(t *testing.T) {
	if Classify("timeout", "") != "cascade" {
		t.Fatal("timeout")
	}
	if Classify("ok", "welcome") != "" {
		t.Fatal("обычная страница не должна уходить в каскад")
	}
	if Classify("ok", "Доступ ограничен") != "cascade" {
		t.Fatal("заглушка")
	}
}
