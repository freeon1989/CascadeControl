package main

import (
	"encoding/json"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"cascade-agent/internal/config"
	"cascade-agent/internal/detect"
	"cascade-agent/internal/failover"
	"cascade-agent/internal/lists"
	"cascade-agent/internal/peers"
	"cascade-agent/internal/xraypatch"
)

type exitStatus struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Up       bool   `json:"up"`
	RTTMs    int64  `json:"rtt_ms"`
	Failures int    `json:"failures"`
	Standby  bool   `json:"standby"`
	Selected bool   `json:"selected"`
}

type statusDoc struct {
	Updated  string         `json:"updated"`
	Strategy string         `json:"strategy"`
	Active   []string       `json:"active"`
	Exits    []exitStatus   `json:"exits"`
	Lists    map[string]int `json:"lists"`
	Detected []detected     `json:"detected"`
	AwgDump  string         `json:"awg_dump,omitempty"`
	Peers    []peers.Peer   `json:"peers,omitempty"`
}

type detected struct {
	Domain string `json:"domain"`
	Status string `json:"status"`
}

func main() {
	configPath := flag.String("config", "/etc/cascade-agent/config.json", "путь к конфигурации")
	statusPath := flag.String("status", "/var/lib/cascade-agent/status.json", "куда писать состояние")
	once := flag.Bool("once", false, "один проход без цикла")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("конфиг: %v", err)
	}
	log.Printf("cascade-agent: стратегия %s, выходов %d, отказоустойчивость %v", cfg.Strategy, len(cfg.Exits), cfg.Failover)
	go serveSubscription(cfg)

	health := map[string]failover.Health{}
	cursor := 0
	for {
		active := pass(cfg, health, cursor, *statusPath)
		_ = active
		cursor++
		if *once {
			return
		}
		time.Sleep(time.Duration(cfg.IntervalSec) * time.Second)
	}
}

func pass(cfg config.File, health map[string]failover.Health, cursor int, statusPath string) []string {
	timeout := time.Duration(cfg.ProbeTimeoutMs) * time.Millisecond
	for _, node := range cfg.Transits {
		up, rtt := dial(node.Host, node.Port, timeout)
		log.Printf("транзит %s %s:%d up=%v rtt=%s", node.Name, node.Host, node.Port, up, rtt)
	}
	for _, exit := range cfg.Exits {
		up, rtt := dial(exit.Host, exit.Port, timeout)
		failover.Observe(health, exit.ID, up, rtt.Milliseconds(), cfg.FailureThreshold)
	}
	view := make([]failover.Exit, 0, len(cfg.Exits))
	for _, exit := range cfg.Exits {
		view = append(view, failover.Exit{ID: exit.ID, Weight: exit.Weight, Standby: exit.Standby})
	}
	active := failover.Choose(view, health, cfg.Strategy, cfg.Failover, cfg.FailureThreshold, cursor)
	if cfg.XrayConfig != "" {
		changed, err := xraypatch.Apply(cfg.XrayConfig, "cascade", active)
		if err != nil {
			log.Printf("xray: %v", err)
		} else if changed && cfg.XrayService != "" {
			if err := exec.Command("systemctl", "restart", cfg.XrayService).Run(); err != nil {
				log.Printf("перезапуск %s: %v", cfg.XrayService, err)
			} else {
				log.Printf("балансировщик обновлён: %v", active)
			}
		}
	}
	counts := refreshLists(cfg)
	found := watchDomains(cfg, timeout)
	dump := peers.Collect()
	writeStatus(statusPath, cfg, health, active, counts, found, dump, peers.ParseDump(dump))
	return active
}

func watchDomains(cfg config.File, timeout time.Duration) []detected {
	var found []detected
	for _, domain := range cfg.WatchDomains {
		up, _ := dial(domain, 443, timeout)
		status := "ok"
		if !up {
			status = "timeout"
		}
		if action := detect.Classify(status, ""); action != "" {
			found = append(found, detected{Domain: domain, Status: status})
			log.Printf("домен %s помечен для каскада (%s)", domain, status)
		}
	}
	return found
}

func dial(host string, port int, timeout time.Duration) (bool, time.Duration) {
	if host == "" || port == 0 {
		return false, 0
	}
	start := time.Now()
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, itoa(port)), timeout)
	if err != nil {
		return false, 0
	}
	_ = conn.Close()
	return true, time.Since(start)
}

func itoa(value int) string {
	if value == 0 {
		return "0"
	}
	var buf [8]byte
	i := len(buf)
	for value > 0 {
		i--
		buf[i] = byte('0' + value%10)
		value /= 10
	}
	return string(buf[i:])
}

func refreshLists(cfg config.File) map[string]int {
	counts := map[string]int{}
	dir := "/var/lib/cascade-agent/lists"
	_ = os.MkdirAll(dir, 0o755)
	client := http.Client{Timeout: 20 * time.Second}
	for _, source := range cfg.Sources {
		if !source.Enabled || source.Kind == "geosite-dat" || source.URL == "" {
			continue
		}
		resp, err := client.Get(source.URL)
		if err != nil {
			log.Printf("список %s: %v", source.ID, err)
			continue
		}
		body := make([]byte, 0, 1<<20)
		buf := make([]byte, 32<<10)
		for len(body) < 8<<20 {
			n, readErr := resp.Body.Read(buf)
			body = append(body, buf[:n]...)
			if readErr != nil {
				break
			}
		}
		_ = resp.Body.Close()
		text := string(body)
		var parsed []string
		if source.Kind == "cidr" {
			parsed = lists.ParseCIDRs(text)
		} else {
			parsed = lists.ParseDomains(text)
		}
		counts[source.ID] = len(parsed)
		_ = os.WriteFile(filepath.Join(dir, source.ID+".txt"), body, 0o644)
	}
	return counts
}

func writeStatus(path string, cfg config.File, health map[string]failover.Health, active []string, counts map[string]int, found []detected, dump string, peerRows []peers.Peer) {
	selected := map[string]bool{}
	for _, id := range active {
		selected[id] = true
	}
	doc := statusDoc{
		Updated:  time.Now().UTC().Format(time.RFC3339),
		Strategy: cfg.Strategy,
		Active:   active,
		Lists:    counts,
		Detected: found,
		AwgDump:  dump,
		Peers:    peerRows,
	}
	for _, exit := range cfg.Exits {
		cur := health[exit.ID]
		doc.Exits = append(doc.Exits, exitStatus{
			ID: exit.ID, Name: exit.Name, Up: cur.Up || !cur.Seen, RTTMs: cur.RTTMs,
			Failures: cur.Failures, Standby: exit.Standby, Selected: selected[exit.ID],
		})
	}
	raw, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return
	}
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return
	}
	_ = os.Rename(tmp, path)
}

func serveSubscription(cfg config.File) {
	if cfg.Subscription.Listen == "" || len(cfg.Subscription.Clients) == 0 {
		return
	}
	tokens := map[string]string{}
	for _, client := range cfg.Subscription.Clients {
		tokens[client.Token] = client.BodyB64
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/sub/", func(w http.ResponseWriter, r *http.Request) {
		token := r.URL.Path[len("/sub/"):]
		body, ok := tokens[token]
		if !ok || token == "" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte(body))
	})
	server := &http.Server{Addr: cfg.Subscription.Listen, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		log.Printf("подписка: %v", err)
	}
}
