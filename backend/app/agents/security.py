from collections import defaultdict
from decimal import Decimal

from ..providers.solana import SYSTEM_PROGRAM, TOKEN_2022, TOKEN_PROGRAM
from ..schemas import SecurityResult


class SecurityAgent:
    def __init__(self, rpc):
        self.rpc = rpc

    async def analyze(self, mint: str) -> SecurityResult:
        r = SecurityResult(
            unknown=[
                "Identidade e saldo do desenvolvedor",
                "LP lock/burn e poderes do pool",
                "Holders totais",
                "Wash trading",
                "Garantia de venda",
            ]
        )
        try:
            account = await self.rpc.account(mint)
            if not account or account.get("owner") not in (TOKEN_PROGRAM, TOKEN_2022):
                r.critical.append("Mint inexistente ou programa não reconhecido")
                return r
            r.program = account["owner"]
            parsed = account.get("data", {}).get("parsed", {})
            info = parsed.get("info", {})
            if parsed.get("type") != "mint" or not all(
                k in info for k in ("mintAuthority", "freezeAuthority", "supply", "decimals")
            ):
                r.unknown.append("Decodificação das permissões do mint")
                return r
            r.authority_checked = True
            r.mint_authority, r.freeze_authority = info["mintAuthority"], info["freezeAuthority"]
            r.decimals = info["decimals"]
            r.extensions = [x.get("extension", "unknown") for x in info.get("extensions", [])]
            if r.mint_authority:
                r.critical.append("Mint authority ativa")
            if r.freeze_authority:
                r.critical.append("Freeze authority ativa")
            # Strict allowlist: Token-2022 remains blocked until extension-aware transfer simulation exists.
            if r.program == TOKEN_2022:
                r.critical.append(
                    "Token-2022: execução bloqueada até auditoria de extensões e transferências"
                )
            supply = Decimal(info["supply"])
            if supply <= 0:
                r.critical.append("Supply inválido")
                return r
            largest = await self.rpc.largest(mint)
            if not largest:
                r.unknown.append("Maiores contas indisponíveis")
                return r
            accounts = await self.rpc.multiple([x["address"] for x in largest])
            owner_amounts = defaultdict(Decimal)
            unresolved = False
            for row, account in zip(largest, accounts, strict=True):
                info_account = (account or {}).get("data", {}).get("parsed", {}).get("info", {})
                owner = info_account.get("owner")
                if not owner or info_account.get("mint") != mint:
                    unresolved = True
                    continue
                owner_amounts[owner] += Decimal(row["amount"])
            owners = list(owner_amounts)
            owner_accounts = await self.rpc.multiple(owners) if owners else []
            for owner, owner_account in zip(owners, owner_accounts, strict=True):
                pct = float(owner_amounts[owner] / supply * 100)
                # A system-owned address can still be a PDA. Do not claim an EOA without curve check.
                kind = "unclassified"
                if owner_account and (
                    owner_account.get("executable") or owner_account.get("owner") != SYSTEM_PROGRAM
                ):
                    kind = "program_or_pool_candidate"
                unresolved = True  # pool registry + owner-wide enumeration not available in free RPC sample
                r.holders.append({"owner": owner, "sample_pct": pct, "classification": kind})
            r.largest_accounts_pct = float(sum(Decimal(x["amount"]) for x in largest[:10]) / supply * 100)
            r.concentration_resolved = not unresolved
            r.unknown.append("Top 10 holders: amostra de contas não resolve pools/PDAs nem owners completos")
            r.notes.append("largest_accounts_pct é top 10 contas SPL brutas; não é top 10 holders.")
        except Exception as exc:
            r.unknown.append(f"Análise on-chain incompleta: {type(exc).__name__}")
        return r

    async def analyze_complete(self, mint: str) -> SecurityResult:
        """Conservative owner-group upper bound, INCLUDING pools; never relabel pools as people.

        Used on demand for supervised live only. Full raw amounts must reconcile
        against immutable mint supply. Restricted/unavailable GPA never approves.
        """
        result = await self.analyze(mint)
        if result.critical or not result.authority_checked:
            return result
        try:
            account = await self.rpc.call(
                "getAccountInfo",
                [mint, {"encoding": "jsonParsed", "commitment": "finalized"}],
                ttl=0,
                priority=0,
            )
            info = account["value"]["data"]["parsed"]["info"]
            if account["value"]["owner"] != TOKEN_PROGRAM or info["mintAuthority"] or info["freezeAuthority"]:
                raise ValueError("Permissões divergentes")
            rows = await self.rpc.call(
                "getProgramAccounts",
                [
                    TOKEN_PROGRAM,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "finalized",
                        "withContext": True,
                        "minContextSlot": account["context"]["slot"],
                        "filters": [{"dataSize": 165}, {"memcmp": {"offset": 0, "bytes": mint}}],
                    },
                ],
                ttl=0,
                priority=0,
            )
            groups = defaultdict(int)
            seen = set()
            if not 0 < len(rows["value"]) <= 50000:
                raise ValueError("Cobertura excede capacidade")
            for row in rows["value"]:
                parsed = row["account"]["data"]["parsed"]
                data = parsed["info"]
                if (
                    row["pubkey"] in seen
                    or row["account"]["owner"] != TOKEN_PROGRAM
                    or parsed["type"] != "account"
                    or data["mint"] != mint
                ):
                    raise ValueError("Enumeração inconsistente")
                seen.add(row["pubkey"])
                amount = int(data["tokenAmount"]["amount"])
                if amount < 0 or data["tokenAmount"]["decimals"] != info["decimals"]:
                    raise ValueError("Quantidade inconsistente")
                groups[data["owner"]] += amount
            supply = int(info["supply"])
            if supply <= 0 or sum(groups.values()) != supply:
                raise ValueError("Amostra não reconcilia supply")
            result.top10_pct = sum(sorted(groups.values(), reverse=True)[:10]) / supply * 100
            result.concentration_resolved = True
            result.unknown = [x for x in result.unknown if not x.startswith("Top 10 holders")]
            result.notes.append(
                "Limite superior conservador: top 10 grupos owner de todas as contas, INCLUINDO pools/PDAs; soma reconciliada ao supply. Não identifica beneficiários nem carteiras relacionadas."
            )
            result.checked_at = __import__("time").time()
        except Exception as exc:
            result.concentration_resolved = False
            result.unknown.append("Enumeração integral não comprovada: " + type(exc).__name__)
        return result
