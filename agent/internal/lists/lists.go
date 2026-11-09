package lists

import (
	"bufio"
	"net"
	"strings"
)

func ParseDomains(text string) []string {
	return parse(text, false)
}

func ParseCIDRs(text string) []string {
	return parse(text, true)
}

func parse(text string, cidr bool) []string {
	seen := map[string]struct{}{}
	var out []string
	scanner := bufio.NewScanner(strings.NewReader(text))
	for scanner.Scan() {
		line := strings.TrimSpace(strings.SplitN(scanner.Text(), "#", 2)[0])
		if line == "" {
			continue
		}
		if cidr {
			if _, _, err := net.ParseCIDR(line); err != nil {
				if ip := net.ParseIP(line); ip != nil {
					line += "/32"
					if ip.To4() == nil {
						line = ip.String() + "/128"
					}
				} else {
					continue
				}
			}
		} else {
			line = strings.TrimPrefix(line, "domain:")
			line = strings.TrimPrefix(line, "full:")
			line = strings.ToLower(strings.TrimSuffix(line, "."))
			if !strings.Contains(line, ".") || strings.Contains(line, " ") {
				continue
			}
		}
		if _, ok := seen[line]; ok {
			continue
		}
		seen[line] = struct{}{}
		out = append(out, line)
	}
	return out
}
