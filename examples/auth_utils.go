package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
)

// HashPassword hashes a password with a salt using SHA-256
func HashPassword(password string, salt string) (string, string) {
	if salt == "" {
		saltBytes := make([]byte, 32)
		rand.Read(saltBytes)
		salt = hex.EncodeToString(saltBytes)
	}
	h := sha256.New()
	h.Write([]byte(password + salt))
	return hex.EncodeToString(h.Sum(nil)), salt
}

// VerifyPassword checks if a password matches the stored hash
func VerifyPassword(password string, storedHash string, salt string) bool {
	computed, _ := HashPassword(password, salt)
	return computed == storedHash
}

// GenerateToken creates a random hex token
func GenerateToken(length int) string {
	if length <= 0 {
		length = 32
	}
	b := make([]byte, length)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// ValidateEmail checks if an email address format is valid
func ValidateEmail(email string) bool {
	pattern := regexp.MustCompile(`^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$`)
	return pattern.MatchString(email)
}

// SanitizeUsername removes dangerous characters from a username
func SanitizeUsername(username string) string {
	re := regexp.MustCompile(`[^a-zA-Z0-9_.\-]`)
	return re.ReplaceAllString(username, "")
}
