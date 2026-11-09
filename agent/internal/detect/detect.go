package detect

import "strings"

var stubMarkers = []string{
	"доступ ограничен",
	"интернет-ресурс заблокирован",
	"rkn.gov.ru",
	"eais.rkn.gov.ru",
	"ресурс заблокирован",
}

// Classify возвращает "cascade", если ответ похож на блокировку, иначе пустую строку.
func Classify(status string, body string) string {
	switch status {
	case "timeout", "reset", "stub":
		return "cascade"
	}
	lower := strings.ToLower(body)
	for _, marker := range stubMarkers {
		if strings.Contains(lower, marker) {
			return "cascade"
		}
	}
	return ""
}
