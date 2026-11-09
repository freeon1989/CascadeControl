package failover

import "testing"

func exits() []Exit {
	return []Exit{
		{ID: "de", Weight: 50},
		{ID: "nl", Weight: 50},
		{ID: "fi", Weight: 0, Standby: true},
	}
}

func TestFailoverPromotesStandbyAfterThreshold(t *testing.T) {
	health := map[string]Health{}
	for i := 0; i < 3; i++ {
		Observe(health, "de", false, 0, 3)
		Observe(health, "nl", false, 0, 3)
	}
	Observe(health, "fi", true, 40, 3)
	got := Choose(exits(), health, "least_ping", true, 3, 0)
	if len(got) != 1 || got[0] != "fi" {
		t.Fatalf("ожидался запасной fi, получено %v", got)
	}
}

func TestLeastPingOrdersByRTT(t *testing.T) {
	health := map[string]Health{}
	Observe(health, "de", true, 80, 3)
	Observe(health, "nl", true, 20, 3)
	got := Choose(exits(), health, "least_ping", true, 3, 0)
	if got[0] != "nl" || got[1] != "de" {
		t.Fatalf("порядок пинга: %v", got)
	}
}

func TestRoundRobinRotates(t *testing.T) {
	got := Choose(exits(), map[string]Health{}, "round_robin", true, 3, 1)
	if got[0] != "nl" {
		t.Fatalf("сдвиг round-robin: %v", got)
	}
}

func TestWeightedPutsHeavierFirst(t *testing.T) {
	items := []Exit{{ID: "a", Weight: 10}, {ID: "b", Weight: 90}}
	got := Choose(items, map[string]Health{}, "weighted", false, 3, 0)
	if got[0] != "b" {
		t.Fatalf("вес: %v", got)
	}
}

func TestUnknownExitStaysUntilThreshold(t *testing.T) {
	health := map[string]Health{}
	Observe(health, "de", false, 0, 3)
	got := Choose(exits(), health, "least_ping", true, 3, 0)
	found := map[string]bool{}
	for _, id := range got {
		found[id] = true
	}
	if !found["de"] || !found["nl"] || found["fi"] {
		t.Fatalf("одна ошибка не должна выключать выход: %v", got)
	}
}
