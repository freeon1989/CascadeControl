// Снимок пиров AmneziaWG. Панель сопоставляет public_key с клиентом.
package peers

import (
	"context"
	"encoding/base64"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type Peer struct {
	PublicKey string `json:"public_key"`
	Handshake int64  `json:"handshake"`
	Rx        int64  `json:"rx"`
	Tx        int64  `json:"tx"`
}

// ParseDump читает `awg show cawg0 dump`. Строка интерфейса отбрасывается:
// во втором поле у неё порт, а не endpoint.
func ParseDump(text string) []Peer {
	var found []Peer
	seen := map[string]bool{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.Split(strings.TrimSpace(line), "\t")
		if len(parts) < 7 || !endpoint(parts[2]) || !wgKey(parts[0]) {
			continue
		}
		handshake, errH := strconv.ParseInt(parts[4], 10, 64)
		rx, errR := strconv.ParseInt(parts[5], 10, 64)
		tx, errT := strconv.ParseInt(parts[6], 10, 64)
		if errH != nil || errR != nil || errT != nil {
			continue
		}
		key := strings.TrimSpace(parts[0])
		if seen[key] {
			continue
		}
		seen[key] = true
		if rx < 0 {
			rx = 0
		}
		if tx < 0 {
			tx = 0
		}
		found = append(found, Peer{PublicKey: key, Handshake: handshake, Rx: rx, Tx: tx})
	}
	return found
}

func endpoint(value string) bool {
	text := strings.TrimSpace(value)
	if text == "" || text == "(none)" {
		return true
	}
	return strings.Contains(text, ":")
}

func wgKey(value string) bool {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
	return err == nil && len(raw) == 32
}

// Collect снимает дамп с контейнера cascade-awg или с awg на хосте.
func Collect() string {
	commands := [][]string{
		{"docker", "exec", "cascade-awg", "awg", "show", "cawg0", "dump"},
		{"awg", "show", "cawg0", "dump"},
	}
	for _, cmd := range commands {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		out, err := exec.CommandContext(ctx, cmd[0], cmd[1:]...).Output()
		cancel()
		if err == nil && strings.Contains(string(out), "\t") {
			return string(out)
		}
	}
	return ""
}
