"""Restricted Jupiter v2 ExactIn compiler. Fail closed on unsupported instructions.
IDL: jup-ag/rfq-v2-sdk f3f30ff2af48f63c11f326a486b8b0eb9611714f,
fill-decoder/idls/aggregator.json. No remote transaction is signed verbatim.
"""

import base64
import json
import os
import stat
from pathlib import Path

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from ..providers.solana import SYSTEM_PROGRAM, TOKEN_PROGRAM

JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
IDL = json.loads(Path(__file__).with_name("jupiter_idl.json").read_text())
TYPES = {t["name"]: t["type"] for t in IDL["types"]}


def associated(owner: str, mint: str) -> str:
    return str(
        Pubkey.find_program_address(
            [
                bytes(Pubkey.from_string(owner)),
                bytes(Pubkey.from_string(TOKEN_PROGRAM)),
                bytes(Pubkey.from_string(mint)),
            ],
            Pubkey.from_string(ATA),
        )[0]
    )


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, n):
        value = self.data[self.offset : self.offset + n]
        if len(value) != n:
            raise ValueError("Instrução truncada")
        self.offset += n
        return value

    def decode(self, spec, depth=0):
        if depth > 16:
            raise ValueError("IDL excedeu profundidade")
        if isinstance(spec, str):
            if spec in ("u8", "u16", "u32", "u64", "u128", "i8", "i16", "i32", "i64", "i128"):
                return int.from_bytes(self.take(int(spec[1:]) // 8), "little", signed=spec[0] == "i")
            if spec == "bool":
                b = self.take(1)[0]
                if b > 1:
                    raise ValueError("Booleano inválido")
                return bool(b)
            if spec == "pubkey":
                return str(Pubkey.from_bytes(self.take(32)))
            raise ValueError("Tipo IDL não suportado")
        if "defined" in spec:
            name = spec["defined"] if isinstance(spec["defined"], str) else spec["defined"]["name"]
            return self.decode(TYPES[name], depth + 1)
        if "vec" in spec:
            length = self.decode("u32")
            if length > 64:
                raise ValueError("Vetor excessivo")
            return [self.decode(spec["vec"], depth + 1) for _ in range(length)]
        if "option" in spec:
            return self.decode(spec["option"], depth + 1) if self.decode("bool") else None
        if "array" in spec:
            return [self.decode(spec["array"][0], depth + 1) for _ in range(spec["array"][1])]
        if spec.get("kind") == "struct":
            return {f["name"]: self.decode(f["type"], depth + 1) for f in spec["fields"]}
        if spec.get("kind") == "enum":
            index = self.decode("u8")
            if index >= len(spec["variants"]):
                raise ValueError("Variante desconhecida")
            variant = spec["variants"][index]
            values = [
                self.decode(f.get("type", f) if isinstance(f, dict) else f, depth + 1)
                for f in variant.get("fields", [])
            ]
            return {"variant": variant["name"], "values": values}
        raise ValueError("Formato IDL não suportado")


def validate_swap(build, owner, input_mint, output_mint, amount, max_slip):
    if (
        build.get("inputMint") != input_mint
        or build.get("outputMint") != output_mint
        or int(build.get("inAmount", 0)) != amount
    ):
        raise ValueError("Cotação diverge da intenção")
    if build.get("swapMode") != "ExactIn" or build.get("transactionVersion", 0) != 0:
        raise ValueError("Apenas ExactIn / transação v0")
    if build.get("cleanupInstruction") or build.get("otherInstructions") or build.get("tipInstruction"):
        raise ValueError("Instruções adicionais não autorizadas")
    swap = build["swapInstruction"]
    if swap["programId"] != JUPITER:
        raise ValueError("Programa não autorizado")
    raw = base64.b64decode(swap["data"], validate=True)
    spec = next((i for i in IDL["instructions"] if bytes(i["discriminator"]) == raw[:8]), None)
    if not spec:
        raise ValueError("Variante Jupiter não auditada")
    reader = Reader(raw[8:])
    args = {a["name"]: reader.decode(a["type"]) for a in spec["args"]}
    if reader.offset != len(reader.data):
        raise ValueError("Bytes extras na instrução")
    if args["in_amount"] != amount or args["quoted_out_amount"] != int(build["outAmount"]):
        raise ValueError("Quantidade codificada diverge da cotação")
    slip = args["slippage_bps"]
    if not 0 <= slip <= max_slip or slip != int(build["slippageBps"]):
        raise ValueError("Slippage não autorizado")
    if args["platform_fee_bps"] or args["positive_slippage_bps"]:
        raise ValueError("Taxa adicional não autorizada")
    if not 0 < len(args["route_plan"]) <= 8:
        raise ValueError("Rota não suportada")
    source, dest = associated(owner, input_mint), associated(owner, output_mint)
    accounts = swap["accounts"]
    if len(accounts) < len(spec["accounts"]):
        raise ValueError("Contas incompletas")
    expected = {
        "user_transfer_authority": owner,
        "user_source_token_account": source,
        "source_token_account": source,
        "user_destination_token_account": dest,
        "destination_token_account": dest,
        "source_mint": input_mint,
        "destination_mint": output_mint,
        "source_token_program": TOKEN_PROGRAM,
        "destination_token_program": TOKEN_PROGRAM,
        "program": JUPITER,
    }
    for name, acc in zip(spec["accounts"], accounts):
        want = name.get("address") or expected.get(name["name"])
        if want and acc["pubkey"] != want and not (name.get("optional") and acc["pubkey"] == JUPITER):
            raise ValueError("Conta da instrução diverge da intenção")
    if any(a.get("isSigner") and a["pubkey"] != owner for a in accounts):
        raise ValueError("Assinante adicional")
    # Only idempotent ATAs for this wallet and the two intended mints.
    for ix in build.get("setupInstructions", []):
        if ix["programId"] != ATA or base64.b64decode(ix["data"]) != b"\x01":
            raise ValueError("Setup não autorizado")
        keys = [a["pubkey"] for a in ix["accounts"]]
        if (
            len(keys) != 6
            or keys[0] != owner
            or keys[2] != owner
            or keys[3] not in (input_mint, output_mint)
            or keys[1] != associated(owner, keys[3])
            or keys[4:] != [SYSTEM_PROGRAM, TOKEN_PROGRAM]
        ):
            raise ValueError("ATA de terceiro ou programa divergente")
        if any(a.get("isSigner") and a["pubkey"] != owner for a in ix["accounts"]):
            raise ValueError("Setup exige outro assinante")
    minimum = args["quoted_out_amount"] * (10000 - slip) // 10000
    if minimum <= 0:
        raise ValueError("Saída mínima inválida")
    return minimum


def compile_review(build, owner, blockhash, micro_price):
    instructions = [set_compute_unit_limit(1_400_000), set_compute_unit_price(micro_price)]
    for ix in [*build.get("setupInstructions", []), build["swapInstruction"]]:
        instructions.append(
            Instruction(
                Pubkey.from_string(ix["programId"]),
                base64.b64decode(ix["data"], validate=True),
                [
                    AccountMeta(Pubkey.from_string(a["pubkey"]), bool(a["isSigner"]), bool(a["isWritable"]))
                    for a in ix["accounts"]
                ],
            )
        )
    # No provider-supplied lookup tables; larger routes fail closed at wire size.
    message = MessageV0.try_compile(Pubkey.from_string(owner), instructions, [], Hash.from_string(blockhash))
    tx = VersionedTransaction(message, [NullSigner(Pubkey.from_string(owner))])
    if len(bytes(tx)) > 1232:
        raise ValueError("Rota excede tamanho suportado; solicite rota menor")
    return base64.b64encode(bytes(tx)).decode()


def sign_review(unsigned: str, filename: str, expected_owner: str) -> tuple[str, str]:
    path = Path(filename).expanduser().resolve(strict=True)
    project = Path(__file__).resolve().parents[3]
    if path.is_relative_to(project):
        raise ValueError("Chave deve ficar fora do repositório")
    if os.name != "posix":
        raise ValueError("Assinatura local requer permissões POSIX verificáveis")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Arquivo de chave deve pertencer ao processo e ter permissão 600")
        raw = os.read(fd, 4096)
    finally:
        os.close(fd)
    key = Keypair.from_bytes(bytes(json.loads(raw)))
    if str(key.pubkey()) != expected_owner:
        raise ValueError("Carteira não corresponde à chave")
    unsigned_tx = VersionedTransaction.from_bytes(base64.b64decode(unsigned, validate=True))
    if (
        unsigned_tx.message.header.num_required_signatures != 1
        or str(unsigned_tx.message.account_keys[0]) != expected_owner
    ):
        raise ValueError("Assinantes divergem")
    tx = VersionedTransaction(unsigned_tx.message, [key])
    return base64.b64encode(bytes(tx)).decode(), str(tx.signatures[0])
