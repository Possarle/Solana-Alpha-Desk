# Verificação da primeira versão

Data: 13/10/2026. Ambiente: Linux, Python 3.12.14, Node 24.19.0. Dependências Python fixadas em requirements.lock e requirements-dev.lock; npm em package-lock.json.

## Testes automatizados

**50 testes passaram.** `pytest -q` e `ruff check app tests` executados. Há um aviso de depreciação do TestClient atual do Starlette sobre HTTPX; não é uma falha funcional. Build TypeScript/Vite concluído.

Cenários verificados:

- Market cap nulo permanece desconhecido mesmo com FDV.
- Autoridades não consultadas, concentração desconhecida e Token-2022 não recebem aprovação indevida.
- Evidência de vendas não substitui segurança.
- Risco crítico não é superado por score social/smart money.
- Best candidate separado de worth trading; NO TRADE.
- Dimensionamento por capital/liquidez/incerteza e limites de configuração.
- Endereços base58 de 32 bytes; bloqueios por rede/endereço.
- Ordem repetida idempotente, inclusive efeito financeiro único.
- Duas entradas concorrentes respeitam a posição única.
- Dados antigos, rejeições, slippage/liq e emergência bloqueiam entradas.
- Stop-loss, take-profit, trailing, tempo máximo e queda de liquidez.
- Preço ausente ou liquidez não executável mantém slot e saída pendente; recuperação tenta de novo.
- Perda diária considera marcação aberta; trava permanece após saída.
- Reconciliação detecta divergência e impede reativação indevida.
- API, autenticação, validação, evidências, carteiras, settings e WebSocket.
- Integração completa com fixtures: discovery → security → judge → shadow → stop → conciliação.
- Risco continua executando após pausar descoberta.
- Token previamente descoberto é revisitado depois de sair do feed de lançamentos.
- Cache não renova timestamp; erro HTTP não se torna sucesso; retry, circuito e prioridade da fila.
- Contas de programas/pools não viram automaticamente holders comuns.
- Transferência de token não é compra; deltas token/USDC são apenas swap provável.
- Rota Jupiter valida mints e quantidade.
- Adaptador live não envia mesmo com flag true; confirmação exige finalização sem erro.
- Reconciliação live retorna divergência de saldo explicitamente.
- Migração sobe, desce e sobe novamente em banco SQLite temporário.
- Relatório exportado marcado como simulado, sem credenciais.

Fixtures ficam somente em `backend/tests`; todos os bancos de teste são temporários e separados do banco de operação. Os exemplos de transações nos testes são sintéticos e não representam operações do mercado.

## Consultas reais realizadas

O processo de diagnóstico consultou quatro serviços, sem API paga nem carteira privada:

| Fonte | Evidência observada nesta execução |
|---|---|
| GeckoTerminal | Resposta válida com 20 pools; parser produziu preço, liquidez, volume, contagens e timestamps. Muitos market caps eram null, preservados. |
| DexScreener | Uma sondagem inicial retornou HTTP 403; a integração HTTPX posteriormente retornou 27 pares. O scanner persistiu tokens e avaliações. |
| Solana RPC | getAccountInfo do mint público de Wrapped SOL retornou jsonParsed com programa, decimals e autoridades. Avaliações adicionais também foram tentadas pelo scanner. |
| Jupiter | Consulta quote-only USDC → Wrapped SOL → USDC retornou rota, amounts e router. Nenhuma transação foi construída com taker, assinada ou transmitida. |

Foram exibidos **47 tokens reais coletados** no navegador durante a validação. Esse número descreve a execução de teste, não é um número fixo do produto e não é promessa de cobertura. O arquivo entregue não inclui banco de mercado nem posições pré-carregadas; ele começa a coletar ao iniciar.

A disponibilidade futura, cotação e frequência das fontes não são garantidas por essa consulta bem-sucedida. A migração e o serviço sob teste não usam atalhos que aprovam ausência de dados.

## Navegador

Chromium headless com backend FastAPI real e build de produção React. Verificados:

- Overview, New Tokens, Alpha Scanner, Smart Money, Wallet Tracker, AI Analysis, Trading Terminal, Open Positions, Trade History, Performance, Risk Manager e Settings.
- Pesquisa por endereço, abertura e aba de segurança no detalhe.
- Salvamento de parâmetros sem relaxar política de segurança.
- Pausa e retomada da descoberta.
- WebSocket aceito e atualização de estado.
- Layout desktop 1440 px e mobile 390 px; sem transbordamento horizontal da página (tabelas têm rolagem própria).
- Nenhum erro JavaScript de página observado.

As capturas usadas na inspeção visual eram temporárias e não integram o pacote.

## Não verificado com infraestrutura real do usuário

- Docker e servidor PostgreSQL (Docker indisponível neste ambiente).
- Windows PowerShell e Codespaces hospedado; scripts/configuração fornecidos.
- Conta Telegram/bot ou Ollama com modelo instalado.
- Lucro/prejuízo de uma carteira específica, histórico completo ou vendabilidade real de qualquer memecoin.
- Operação live: ausente e bloqueada; nunca foi executada.

Não existe evidência de lucratividade da estratégia. O padrão estrito de concentração impede entradas quando o dado não está resolvido. Os testes provam propriedades do software nos cenários cobertos, não segurança econômica dos ativos.
