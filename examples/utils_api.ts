// API utilities — TypeScript module B (written by AI agent separately)
import * as crypto from 'crypto';

export function createPasswordHash(pwd: string, saltValue: string | null = null): { hash: string; salt: string } {
    if (saltValue === null) {
        saltValue = crypto.randomBytes(32).toString('hex');
    }
    const result = crypto.pbkdf2Sync(pwd, saltValue, 100000, 64, 'sha256');
    return { hash: result.toString('hex'), salt: saltValue };
}

export function checkPassword(pwd: string, storedHash: string, saltValue: string): boolean {
    const { hash } = createPasswordHash(pwd, saltValue);
    return hash === storedHash;
}

export function makeRandomToken(size: number = 32): string {
    return crypto.randomBytes(size).toString('hex');
}

export function isValidEmail(addr: string): boolean {
    const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    return emailRegex.test(addr);
}

export function cleanUsername(name: string): string {
    return name.replace(/[^a-zA-Z0-9_.-]/g, '');
}

export function formatApiResponse(data: any, status: number = 200): object {
    return {
        status,
        data,
        ok: status >= 200 && status < 300,
    };
}
