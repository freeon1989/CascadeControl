package config

import (
	"encoding/json"
	"os"
)

type Exit struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Weight   int    `json:"weight"`
	Standby  bool   `json:"standby"`
}

type Transit struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Host string `json:"host"`
	Port int    `json:"port"`
}

type Source struct {
	ID      string `json:"id"`
	URL     string `json:"url"`
	Kind    string `json:"kind"`
	Enabled bool   `json:"enabled"`
}

type SubClient struct {
	Token   string `json:"token"`
	BodyB64 string `json:"body_b64"`
}

type Subscription struct {
	Listen  string      `json:"listen"`
	Path    string      `json:"path"`
	Clients []SubClient `json:"clients"`
}

type File struct {
	IntervalSec      int            `json:"interval_sec"`
	Strategy         string         `json:"strategy"`
	Failover         bool           `json:"failover"`
	FailureThreshold int            `json:"failure_threshold"`
	XrayConfig       string         `json:"xray_config"`
	XrayService      string         `json:"xray_service"`
	ProbeTimeoutMs   int            `json:"probe_timeout_ms"`
	Transits         []Transit      `json:"transits"`
	Exits            []Exit         `json:"exits"`
	Sources          []Source       `json:"sources"`
	Subscription     Subscription   `json:"subscription"`
	WatchDomains     []string       `json:"watch_domains"`
}

func Load(path string) (File, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return File{}, err
	}
	var cfg File
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return File{}, err
	}
	if cfg.IntervalSec <= 0 {
		cfg.IntervalSec = 30
	}
	if cfg.FailureThreshold <= 0 {
		cfg.FailureThreshold = 3
	}
	if cfg.ProbeTimeoutMs <= 0 {
		cfg.ProbeTimeoutMs = 3000
	}
	if cfg.Strategy == "" {
		cfg.Strategy = "least_ping"
	}
	return cfg, nil
}
