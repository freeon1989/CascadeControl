package peers

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestParseDumpIgnoresInterfaceLine(t *testing.T) {
	client := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("k", 32)))
	serverPriv := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("p", 32)))
	serverPub := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("s", 32)))
	dump := serverPriv + "\t" + serverPub + "\t51820\toff\t4\t10\t50\n" +
		client + "\t(none)\t203.0.113.8:4242\t10.10.1.2/32\t1700000000\t1000\t2000\toff\n"
	got := ParseDump(dump)
	if len(got) != 1 {
		t.Fatalf("ожидался один пир, получено %d", len(got))
	}
	if got[0].PublicKey != client || got[0].Handshake != 1700000000 || got[0].Rx != 1000 || got[0].Tx != 2000 {
		t.Fatalf("пир разобран неверно: %+v", got[0])
	}
}
