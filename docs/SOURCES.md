# Fontes técnicas consultadas

Documentação oficial consultada em 13/10/2026 e revisada em 14/10/2026 (v0.4). Serviços podem alterar limites e acesso; o painel registra respostas e falhas. Nenhum plano pago é instalado.

| Fonte | Integração | Limite/documentação | Orçamento aplicado |
|---|---|---|---|
| DexScreener | `/token-profiles/latest/v1`, `/tokens/v1/solana/{addresses}` (descoberta, revisita e coorte, até 30 endereços), `/latest/dex/pairs/solana/{pool}` | Perfis 60/min; pares/tokens 300/min | 30/min compartilhados; coorte ≤ 1/min |
| GeckoTerminal v2 | `/networks/solana/new_pools`, `/networks/solana/pools/{pool}`, `/networks/solana/tokens/{mint}/pools`; include=base_token | Documentação pública consultada informa aproximadamente 10/min, variável, cache de 1 min | 8/min |
| Solana RPC | getAccountInfo, getMultipleAccounts (jsonParsed e base64 para estado de pools), getTokenLargestAccounts, getSignaturesForAddress, getTransaction, getTokenAccountsByOwner, getSignatureStatuses | Endpoint público compartilha limites/IP; pode limitar ou negar requisições | 30/min compartilhados |
| Jupiter v2 | `GET https://api.jup.ag/swap/v2/order`, sem taker: somente cotação | Keyless 0,5 rps / 30 por min; chave gratuita opcional 1 rps | 20/min |
| Ollama | `POST /api/generate`, stream=false, schema JSON | Instalação local opcional | Uma inferência por avaliação elegível; timeout 12s |
| Telegram | `POST /bot{token}/sendMessage` | Bot próprio; configuração opcional | 15/min |

- DexScreener API: https://docs.dexscreener.com/api/reference
- GeckoTerminal API: https://api.geckoterminal.com/docs/index.html
- GeckoTerminal guia: https://apiguide.geckoterminal.com/
- Solana métodos RPC: https://solana.com/docs/rpc/http
- Maiores contas SPL: https://solana.com/docs/rpc/http/gettokenlargestaccounts
- Transações confirmadas: https://solana.com/docs/rpc/http/gettransaction
- Clusters/limites públicos: https://solana.com/docs/references/clusters
- Extensões: https://solana.com/docs/tokens/extensions
- Jupiter order/execute: https://developers.jup.ag/docs/swap/order-and-execute
- Jupiter rate limits: https://developers.jup.ag/docs/portal/rate-limits
- Ollama generate: https://docs.ollama.com/api/generate
- Telegram Bot API: https://core.telegram.org/bots/api#sendmessage

A disponibilidade desses endpoints não equivale a licença irrestrita de redistribuição de dados. O pacote é uma estação local de pesquisa; confira os termos de cada fonte antes de hospedar um serviço público ou revender dados.

Não há integração com API privada da Axiom/Cented, extração de sessão do X, scraping de conteúdo protegido ou indexador pago.
