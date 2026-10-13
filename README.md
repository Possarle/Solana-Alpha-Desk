# SOLANA ALPHA DESK

Estação local de pesquisa de memecoins Solana: dados públicos reais, decisões explicáveis e operações **SHADOW**, sem carteira privada e sem IA paga. Primeira versão funcional; não é um robô pronto para movimentar dinheiro real.

## Obter o projeto

```bash
git clone https://github.com/Possarle/Solana-Alpha-Desk.git
```

No GitHub Codespaces, abra **Code → Codespaces → Create codespace on main**. O projeto já está na pasta do repositório; execute `bash scripts/start.sh`.

## Iniciar em poucos comandos

Requisitos: Python **3.12+**, Node **22+**, npm e acesso à internet. As versões Python fixadas foram verificadas com Python 3.12/Linux. Não há assinaturas obrigatórias.

### Linux, macOS ou GitHub Codespaces

```bash
cd Solana-Alpha-Desk
bash scripts/start.sh
```

Abra **http://localhost:8000**. O script cria `.env` e ambiente virtual, instala dependências, compila o React, executa as migrações e inicia o servidor. O primeiro ciclo começa automaticamente. Dependendo das fontes/RPC, uma rodada pode demorar mais que 90 segundos; esse intervalo é contado depois que o ciclo termina.

### Windows PowerShell

```powershell
cd Solana-Alpha-Desk
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Python 3.12 deve estar instalado no launcher `py`, e Node 22+ no PATH. Abra http://localhost:8000. O caminho Windows está fornecido, mas não foi executado neste ambiente Linux.

### Docker com SQLite

```bash
cp .env.example .env
docker compose up --build
```

Abra http://localhost:8000. Dados ficam no volume `alpha_data`. Não use `docker compose down -v` se quiser manter seu histórico.

### Docker com PostgreSQL

```bash
cp .env.example .env
# Defina POSTGRES_PASSWORD no .env antes de usar fora de um ambiente local.
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Docker/PostgreSQL foram configurados, mas não executados aqui: o ambiente de verificação não disponibiliza Docker. O banco SQLite, suas migrações e transações foram executados e testados.

### Codespaces

Incluída configuração `.devcontainer/devcontainer.json`. Execute `bash scripts/start.sh`, abra a porta 8000 na aba **Ports** e mantenha sua visibilidade **Private**. Defina `DASHBOARD_TOKEN` no `.env` para exigir uma senha de sessão no dashboard. A página e a API utilizam a mesma origem. Para desenvolvimento separado em Vite, adicione a origem externa exata a `ALLOWED_ORIGINS`.

## Como usar

1. Aguarde o scanner coletar pools do GeckoTerminal e perfis/pools do DexScreener. Ambos são fontes independentes. Não existe carga automática de tokens fictícios.
2. Overview apresenta capital simulado inicial de **US$ 10.000**, integridade das fontes, decisões e alertas. Isso não representa saldo de uma carteira.
3. Alpha Scanner permite pesquisar endereço/nome, filtrar aprovados/rejeitados e ordenar por score, liquidez ou idade. Clique numa linha para ver motivos, regras, lacunas, preços coletados e segurança.
4. Em Settings, ajuste os parâmetros de pesquisa. Todos são persistidos no banco. Salvar invalida bloqueios temporários; o próximo ciclo reavalia. Os tetos de exposição continuam limitados a 1% por posição, com uma posição simultânea.
5. Wallet Tracker cadastra até cinco **endereços públicos**. A consulta ocorre a cada 120 segundos após o ciclo. Não há carteiras de influenciadores inventadas ou previamente classificadas como rentáveis.
6. No detalhe do token, a aba Social registra conta, URL e evidência manual. Nenhum scraping do X é realizado; evidências manuais não recebem selo de autenticidade.
7. Trading Terminal permite consultar ida/volta no Jupiter e registrar uma entrada shadow **somente quando aprovada**. As entradas automáticas também passam pelos mesmos limites.
8. Risk Manager pausa entradas, tenta encerrar posições, reconcilia caixa e mostra bloqueios. **Pausar descoberta não pausa o risco.**
9. Performance apresenta P&L simulado, marcação de posições, curva patrimonial, drawdown, taxa de acerto, expectativa e custos, com exportação de relatório JSON. Sem operações, métricas ficam vazias — não são preenchidas com retornos hipotéticos.

### Por que pode aparecer NO TRADE constantemente?

A política inicial exige uma concentração de holders resolvida. O RPC gratuito fornece as 20 maiores **contas de tokens**, mas isso não resolve owners completos, PDAs e reservas de pools. Esta versão mantém `top10_pct=null` e `concentration_resolved=false`; **por isso a política estrita bloqueia entradas**. Isso é deliberado, e não evidência de que o token é inseguro ou seguro.

Para estudar a simulação com dados incompletos, você pode desmarcar **Exigir concentração resolvida** em Settings. Essa opção é limitada ao modo shadow; preserva a classificação de risco alto e reduz o orçamento pela metade. Não resolve as lacunas. Outros filtros, mint/freeze authority e score continuam obrigatórios. Mesmo com essa opção, pode não haver oportunidades.

Market cap ausente também bloqueia: FDV é mostrado separadamente e não o substitui. A idade é do pool observado, não necessariamente do mint. A descoberta cobre uma amostra dos lançamentos; não é um firehose de toda a Solana.

## Funcionalidades implementadas

- FastAPI + Pydantic; React/TypeScript + Vite + Tailwind + Recharts; WebSocket com fallback de polling.
- Adaptadores HTTPX com orçamento conservador, fila por prioridade, timeout, cache com timestamp original, retries e circuit breaker.
- Discovery independente e Copy Intelligence compartilhando a avaliação de segurança e risco.
- Reavaliação de tokens que saíram do feed de lançamentos, em lotes limitados para respeitar fontes gratuitas.
- Verificação de mint/freeze e programa; Token-2022 bloqueado por política nesta versão. Amostra de owners sem fingir distribuição completa de holders.
- Julgamento tipado, componentes de score, motivos, desconhecidos, regras e inferências locais separadas.
- Seleção com `best_candidate` e `worth_trading` independentes; `NO TRADE` é um resultado válido.
- Histórico de preços **coletados pelo sistema**; não são candles históricos reconstruídos ou cotações de exchange em tempo real.
- Shadow com custos/slippage estimados, idempotência persistente, reserva de posição atômica, limite diário, stop, alvo, trailing, tempo máximo, queda de liquidez e emergência.
- Saída pendente preserva a posição até haver condições mínimas para registrar um encerramento simulado.
- Monitoramento de carteiras com deltas de token/USDC, swaps prováveis, recorrência, coincidência em janela de 10 min e relatório parcial de inventário/custo médio.
- Migração Alembic versionada, configurações persistentes, logs JSON sem segredos e reconciliação de caixa.
- Ollama opcional, resposta tipada e prazo de 12 s; falta de modelo nunca impede regras determinísticas.
- Telegram opcional com bot próprio, ativado somente ao preencher token e chat no backend.

## O que ainda não está completo

| Área | Limitação explícita |
|---|---|
| Segurança | Sem auditoria completa de programas de pools, LP lock/burn, identificação de desenvolvedor, grafo de carteiras ou detecção comprovada de wash trading. |
| Concentração | Top 10 holders real e número total de holders não resolvidos; apenas amostra de contas SPL. Não existe aprovação estrita de concentração nesta versão. |
| Token-2022 | Nome das extensões observado quando o RPC retorna jsonParsed, mas transferências não são simuladas por extensão; entradas bloqueadas. |
| Carteiras | Últimas 10 assinaturas por ciclo; lacunas, histórico incompleto, transferências e posições anteriores afetam o relatório. Operações em SOL nativo sem contraparte USDC ficam não classificadas. Sem ranking de “carteiras lucrativas”. |
| Social | Formulário de evidências; idade de conta, engajamento, reciclagem e atividade de GitHub não são coletados automaticamente. Score social permanece desconhecido. |
| Simulação | Não modela ordem em livro, MEV, gas real, todos os custos de ATA/rent, transfer fees, impostos nem garantia de preenchimento. Paper e shadow compartilham um estimador; apenas shadow está exposto no dashboard. |
| Performance | Comparação “com e sem filtros” indisponível até existir uma coorte contrafactual acompanhada. Sem backtest retrospectivo ou aprendizado que altere parâmetros automaticamente. |
| LIVE | Adaptador de consulta/reconciliação/confirmação apenas. **Não há assinatura ou broadcast**, nem endpoint para ligar live. `LIVE_ENABLED=true` não altera essa trava. |
| Operação | Uma instância / um worker; não é um cluster de alta disponibilidade. Sem retenção automática de histórico; planeje backups e espaço em disco. |
| Integrações | Telegram/Ollama preparados mas não testados com credenciais/modelo do usuário; PostgreSQL/Docker não executados neste ambiente. |

## Testes

```bash
bash scripts/test.sh
```

Ou, separadamente:

```bash
.venv/bin/pip install -r backend/requirements-dev.lock
cd backend
../.venv/bin/pytest -q
../.venv/bin/ruff check app tests
cd ../frontend
npm ci
npm run build
```

A suíte é offline: respostas externas simuladas ficam **somente nos testes**, com bancos temporários. Não são inseridas no banco de operação. Veja `docs/VERIFICATION.md` para resultados de execução, consulta real e validação de interface.

## Desenvolvimento

Backend:

```bash
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Frontend em outro terminal:

```bash
cd frontend
npm run dev
```

No desenvolvimento separado, copie `.env.example` para `backend/.env` ou exporte as variáveis; o script principal já carrega o `.env` da raiz. Evite `--reload` ou múltiplos workers com o scheduler de produção. Nunca execute duas instâncias sobre o mesmo banco. O lock local do scheduler não é um lock distribuído.

OpenAPI: http://localhost:8000/docs. Saúde: `/healthz`. Estado: `/api/state`. WebSocket: `/ws`. A autenticação WebSocket vai no primeiro frame, não na URL.

## Estrutura

```text
backend/app/
  agents/        discovery, security, smart_money, social, judge,
                 pick, position_size, risk, filter, bench
  providers/     HTTP resiliente, DexScreener, GeckoTerminal, Solana, Jupiter
  execution/     shadow, paper e preparação live isolada
  thresholds.py  parâmetros e validações
  schemas.py     contratos Pydantic
  db.py          15 tabelas persistentes
  engine.py      ciclos independentes de descoberta, risco e carteiras
  main.py        API, autenticação e WebSocket
  performance.py relatórios estimados
backend/tests/   testes unitários, de integração e risco
backend/migrations/ migração Alembic congelada
frontend/src/    dashboard React, tipos e estilos
scripts/         início local, migração e testes
.devcontainer/   configuração Codespaces
```

## Metodologia, custos e fontes

Veja `docs/ARCHITECTURE.md`, `docs/SOURCES.md` e `docs/VERIFICATION.md`. Nenhuma configuração ou pontuação foi calibrada como probabilidade de lucro. Este pacote não é uma recomendação para comprar os tokens exibidos.


## v0.2 — Agent Desk: mesa operacional

A tela inicial **Agent Desk** acompanha CRAWLER, SCAN, VET, SOCIAL, SMART,
JUDGE, CHIEF, SIZE, FILLS e RISK. Os estados vêm do motor Python por WebSocket
(com polling de recuperação), não de uma animação com números aleatórios.
Clique em um agente para ver a responsabilidade e suas últimas atividades.
Animação aparece somente durante execução observada; conexão antiga interrompe-a.

- O token em foco acompanha a posição ou o candidato. O seletor permite investigar outro token.
- O checklist mostra valor observado, limite e resultado PASS/BLOCK/N/D da avaliação.
- O CHIEF distingue o melhor candidato, a aprovação e o resultado da tentativa de entrada.
- O SIZE exibe o último plano da decisão: orçamento, incerteza, custos, stop e alvo estimados.
- O RISK e o histórico persistente mostram acompanhamento, saídas e bloqueios.
- Antes da entrada automática, mercado e segurança são reavaliados. Falhas, mudanças
  de permissões, dados antigos ou mudança de configuração bloqueiam a tentativa.
- Decisões, planos, parâmetros usados e avaliações ficam no banco existente. Não há migração
  destrutiva. Os últimos 80 eventos de atividade são transitórios e reiniciam com o processo;
  decisões, avaliações, ordens e eventos operacionais permanecem persistidos.

### Atualizar uma instalação existente no Codespaces

No terminal que executa o servidor, pressione Ctrl+C. Depois, na raiz do repositório:

```bash
git pull --ff-only origin main
bash scripts/start.sh
```

Abra a porta 8000 e atualize o navegador. Se o Git avisar que existem alterações locais,
preserve essas alterações antes de resolver o conflito; não utilize reset destrutivo.

### Por que pode continuar sem comprar na simulação?

A política padrão exige concentração resolvida. A amostra de maiores contas SPL do RPC
não resolve a distribuição por holder nem identifica todos os pools/PDAs: essa checagem
continua **desconhecida** e, portanto, bloqueia entradas no perfil estrito.

Para um experimento explicitamente menos restritivo, o usuário pode desmarcar
**Exigir concentração resolvida** em Settings e salvar. Isso muda apenas a pesquisa
SHADOW: a lacuna permanece visível, o tamanho é reduzido e todas as outras verificações
continuam ativas. Não garante entradas nem comprova segurança ou rentabilidade.
A atualização não relaxa essa configuração automaticamente.

### Relação com o vídeo de referência

A implementação segue o fluxo de módulos de coleta, filtro, avaliação, seleção,
alocação, execução e risco. Não reproduz o código privado do vídeo nem seus resultados.
O visual usa estações de agentes com estados observados, não uma sala 3D.
Social é manual e opcional; Ollama é opcional e não autoriza ordens. Não há scraping do X,
conversas inventadas entre agentes ou execução live. O projeto não opera quando o
Codespaces está parado.
