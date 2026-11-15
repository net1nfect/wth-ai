---
name: solidity-audit
description: Gunakan saat mengaudit kontrak Solidity, repo Foundry/Hardhat, atau memverifikasi temuan smart contract. Mencakup build/test Foundry, Slither, reproduksi PoC, dan disiplin severity.
---

# Solidity Audit Doctrine

Tujuan: temuan yang **bisa direproduksi**, bukan daftar kecurigaan dari grep.

## Urutan kerja

1. **Petakan scope**: catat commit, kontrak in-scope, dan asumsi trust (admin, oracle, token decimals).
2. **Cek toolchain** dengan `solidity_audit(action="doctor")`. Jangan klaim Slither berjalan bila binary tidak ada.
3. **Build/test** dengan `solidity_audit(action="build"|"test", path="<repo>")`; untuk satu file gunakan `match_path`.
4. **Slither** dengan `solidity_audit(action="slither", path="<repo>")`; anggap output sebagai kandidat, bukan finding.
5. **Baca kode di sekitar sinyal**: validasi reachability, permission, dan asumsi input.
6. **Buat PoC Foundry** di test terpisah. Jalankan test itu dan kutip hasil pass/fail sebenarnya.
7. **Uji batas**: token 0-decimal, supply ekstrem, rounding, unchecked block, delegatecall, reentrancy, dan oracle failure.
8. **Baru tulis finding** setelah ada bukti reproducible; sertakan path, baris, preconditions, impact, dan fix.

## Aturan severity

- **Critical/High** hanya bila ada kehilangan dana/akses nyata dan PoC berhasil.
- **Medium** untuk DoS/akuntansi yang berdampak nyata tetapi butuh kondisi khusus.
- **Low** untuk edge/griefing dengan dampak terbatas; **Info** untuk dokumentasi/design risk.
- Jangan menaikkan severity karena kata "overflow", "reentrancy", atau "oracle" saja.

## Integrasi tool

- `solidity_audit` membentuk argv deterministik dan menjalankannya lewat `run_shell`; permission/approval/transcript tetap berlaku.
- Jangan menyusun command shell manual dari input model bila tool sudah tersedia.
- `forge` dan `slither` dicari dari `PATH`, `FORGE_BIN`/`SLITHER_BIN`, lalu `$WTH_HOME/tools/bin`.
- Untuk anvil/fork, gunakan `run_shell` dengan scope dan approval yang sama; jangan mengarah ke mainnet tanpa izin.

## Format jawaban

Ringkas: scope, commit, test yang dijalankan, finding + severity, bukti/PoC, fix, dan item yang belum diverifikasi.
