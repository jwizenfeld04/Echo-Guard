// Auth utilities — JavaScript module A
const crypto = require('crypto');

function hashPassword(password, salt = null) {
    if (salt === null) {
        salt = crypto.randomBytes(32).toString('hex');
    }
    const hash = crypto.pbkdf2Sync(password, salt, 100000, 64, 'sha256');
    return { hash: hash.toString('hex'), salt };
}

function verifyPassword(password, storedHash, salt) {
    const { hash } = hashPassword(password, salt);
    return crypto.timingSafeEqual(Buffer.from(hash), Buffer.from(storedHash));
}

function generateToken(length = 32) {
    return crypto.randomBytes(length).toString('hex');
}

function validateEmail(email) {
    const pattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    return pattern.test(email);
}

function sanitizeUsername(username) {
    return username.replace(/[^a-zA-Z0-9_.-]/g, '');
}

module.exports = { hashPassword, verifyPassword, generateToken, validateEmail, sanitizeUsername };
