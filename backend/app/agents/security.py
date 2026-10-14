import base64
from collections import defaultdict
from decimal import Decimal

from solders.pubkey import Pubkey

from ..providers.solana import SYSTEM_PROGRAM, TOKEN_2022, TOKEN_PROGRAM
from ..schemas import SecurityResult


def _bytes(address: str) -> bytes | None:
    try:
        return bytes(Pubkey.from_string(address))
    except (ValueError, TypeError):
        return None


def _classify_owner(owner: str, owner_account: dict | None, pool_programs: set[str]) -> str:
    """Never relabel a program address as a person, nor an unknown PDA as liquidity."""
    if owner_account and owner_account.get("owner") in pool_programs:
        return "same_dex_program_account"
    try:
        on_curve = Pubkey.from_string(owner).is_on_curve()
    except (ValueError, TypeError):
        return "unclassified"
    if owner_account and (owner_account.get("executable") or owner_account.get("owner") != SYSTEM_PROGRAM):
        return "program_or_pool_candidate"
    # Off-curve = program-derived (lock, bonding curve, other pool, multisig vault...): counted.
    return "wallet" if on_curve else "program_or_pool_candidate"


class SecurityAgent:
    def __init__(self, rpc):
        self.rpc = rpc

    async def analyze(self, mint: str, pools: list[str] | None = None) -> SecurityResult:
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
            r.largest_accounts_pct = float(sum(Decimal(x["amount"]) for x in largest[:10]) / supply * 100)
            r.notes.append("largest_accounts_pct é top 10 contas SPL brutas; não é top 10 holders.")
            # Pool linkage is proven only by reading the observed pool accounts themselves.
            pool_data = await self._pool_accounts(pools or [])
            pool_programs = {p["program"] for p in pool_data.values()}
            owner_amounts: dict[str, Decimal] = defaultdict(Decimal)
            pool_amount = Decimal(0)
            unresolved = False
            for row, account in zip(largest, accounts, strict=True):
                info_account = (account or {}).get("data", {}).get("parsed", {}).get("info", {})
                owner = info_account.get("owner")
                if not owner or info_account.get("mint") != mint:
                    unresolved = True
                    continue
                vault = _bytes(row["address"])
                if owner in pool_data or any(vault and vault in p["raw"] for p in pool_data.values()):
                    pool_amount += Decimal(row["amount"])
                    r.holders.append(
                        {
                            "owner": owner,
                            "account": row["address"],
                            "sample_pct": float(Decimal(row["amount"]) / supply * 100),
                            "classification": "verified_pool_vault",
                            "counted": False,
                        }
                    )
                    continue
                owner_amounts[owner] += Decimal(row["amount"])
            owners = list(owner_amounts)
            owner_accounts = await self.rpc.multiple(owners) if owners else []
            counted: dict[str, Decimal] = {}
            for owner, owner_account in zip(owners, owner_accounts, strict=True):
                pct = float(owner_amounts[owner] / supply * 100)
                kind = _classify_owner(owner, owner_account, pool_programs)
                if kind != "same_dex_program_account":
                    counted[owner] = owner_amounts[owner]
                r.holders.append(
                    {
                        "owner": owner,
                        "sample_pct": pct,
                        "classification": kind,
                        "counted": kind != "same_dex_program_account",
                    }
                )
            r.pool_pct = float(pool_amount / supply * 100) if pool_data else None
            if not pool_data:
                unresolved = True
                r.unknown.append(
                    "Top 10 holders: pool observado não pôde ser lido; cofres de liquidez não separados"
                )
            elif not unresolved:
                self._resolve_upper_bound(r, largest, counted, supply)
            else:
                r.unknown.append("Top 10 holders: conta da amostra sem owner/mint verificável")
            r.concentration_resolved = not unresolved
        except Exception as exc:
            r.unknown.append(f"Análise on-chain incompleta: {type(exc).__name__}")
        return r

    async def _pool_accounts(self, pools: list[str]) -> dict[str, dict]:
        """Raw pool state for each observed pool; only successfully read pools are returned."""
        pools = [p for p in dict.fromkeys(pools) if _bytes(p)][:5]
        if not pools:
            return {}
        result = await self.rpc.call(
            "getMultipleAccounts", [pools, {"encoding": "base64", "commitment": "confirmed"}], ttl=300
        )
        found = {}
        for pool, value in zip(pools, result["value"], strict=True):
            data = (value or {}).get("data")
            if not value or not isinstance(data, list) or not data or not value.get("owner"):
                continue
            try:
                found[pool] = {"program": value["owner"], "raw": base64.b64decode(data[0])}
            except (ValueError, TypeError):
                continue
        return found

    @staticmethod
    def _resolve_upper_bound(r: SecurityResult, largest, counted: dict[str, Decimal], supply: Decimal):
        """Upper bound of top-10 owner share from the RPC's 20 largest token accounts.

        Every account outside the sample holds at most the smallest sampled amount, so a
        single-account holder outside the sample can contribute at most that amount.
        """
        groups = sorted(counted.values(), reverse=True)[:10]
        complete = len(largest) < 20
        floor = Decimal(0) if complete else min(Decimal(x["amount"]) for x in largest)
        bound = sum(groups, Decimal(0)) + floor * max(0, 10 - len(groups))
        r.top10_pct = min(100.0, float(bound / supply * 100))
        r.concentration_method = "complete_accounts" if complete else "top20_upper_bound"
        excluded = sum(1 for h in r.holders if not h["counted"])
        r.notes.append(
            "Top 10 = limite superior: maiores owners da amostra"
            + ("" if complete else " + contas fora da amostra limitadas ao menor saldo amostrado")
            + f"; {excluded} conta(s) de liquidez verificada(s) excluída(s)."
        )
        r.unknown.append(
            "Top 10 holders: owners com várias contas pequenas e carteiras relacionadas não identificados"
        )

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
