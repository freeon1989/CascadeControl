// Выбор живых выходов. Неизвестный узел считается живым, пока не наберёт порог ошибок,
// иначе первый запуск погасил бы весь каскад.
package failover

import "sort"

type Exit struct {
	ID      string
	Weight  int
	Standby bool
}

type Health struct {
	Failures int
	Up       bool
	Seen     bool
	RTTMs    int64
}

func Observe(health map[string]Health, id string, up bool, rttMs int64, threshold int) {
	cur := health[id]
	cur.Seen = true
	if up {
		cur.Failures = 0
		cur.Up = true
		cur.RTTMs = rttMs
	} else {
		cur.Failures++
		cur.Up = cur.Failures < threshold
	}
	health[id] = cur
}

func alive(health map[string]Health, id string, failover bool, threshold int) bool {
	if !failover {
		return true
	}
	cur, ok := health[id]
	if !ok || !cur.Seen {
		return true
	}
	if cur.Failures >= threshold {
		return false
	}
	return cur.Up || cur.Failures < threshold
}

// Choose возвращает id выходов в том порядке, в каком их нужно поставить в балансировщик.
func Choose(exits []Exit, health map[string]Health, strategy string, failover bool, threshold int, cursor int) []string {
	pool := make([]Exit, 0, len(exits))
	for _, exit := range exits {
		if exit.Standby {
			continue
		}
		if alive(health, exit.ID, failover, threshold) {
			pool = append(pool, exit)
		}
	}
	if len(pool) == 0 && failover {
		for _, exit := range exits {
			if exit.Standby && alive(health, exit.ID, failover, threshold) {
				pool = append(pool, exit)
			}
		}
	}
	if len(pool) == 0 {
		for _, exit := range exits {
			if !exit.Standby {
				pool = append(pool, exit)
			}
		}
	}
	switch strategy {
	case "weighted":
		sort.SliceStable(pool, func(i, j int) bool { return pool[i].Weight > pool[j].Weight })
	case "round_robin":
		if len(pool) > 0 && cursor != 0 {
			shift := cursor % len(pool)
			if shift < 0 {
				shift += len(pool)
			}
			pool = append(pool[shift:], pool[:shift]...)
		}
	default:
		sort.SliceStable(pool, func(i, j int) bool {
			left, lok := health[pool[i].ID]
			right, rok := health[pool[j].ID]
			if !lok || !left.Seen {
				return false
			}
			if !rok || !right.Seen {
				return true
			}
			return left.RTTMs < right.RTTMs
		})
	}
	ids := make([]string, 0, len(pool))
	for _, exit := range pool {
		ids = append(ids, exit.ID)
	}
	return ids
}
