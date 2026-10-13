# Arquitetura e metodologia

## Fluxo

1. GeckoTerminal lista pools novos; DexScreener lista perfis recentes e pares. Até 20 endereços antigos são revisitados por lote. Isso é amostragem e pode perder lançamentos.
2. Observação completa de um pool é preservada com fonte e instante de coleta. Para duplicatas, usa-se o pool mais líquido; campos de snapshots distintos não são mesclados para fabricar uma observação.
3. Filtros baratos verificam idade, liquidez, volume, market cap, transações e vendas recentes. Rejeições recebem endereço/rede, motivos e duração em `blocks`.
4. Candidatos fazem consulta on-chain, com limite de 30 s por análise. Campos ausentes permanecem desconhecidos. Token-2022 é bloqueado em vez de assumir compatibilidade.
5. Judge recebe objetos estruturados; registra scores, regras, riscos e lacunas. Ollama recebe apenas números/status, sem texto social não confiável. Sua saída não altera aprovação.
6. Pick classifica o melhor observado e, separadamente, verifica se existe algum aprovado.
7. Position Size aplica o menor de: patrimônio × exposição máxima × fator de incerteza, liquidez × 0,0005 e caixa.
8. Shadow registra ordem e posição na mesma transação. O risco consulta preço em ciclo independente e só libera o slot após registrar saída simulada executável pelo modelo.

## Score v1

- Mercado: `min(100, liquidez/min_liquidez*30 + volume24/min_volume24*15)`.
- Momentum: `clip(40 + variação5m*2 + min(30, compras5m/max(vendas5m,1)*10), 0,100)`.
- Segurança: 0 para risco crítico/permissões não lidas; 45 para concentração desconhecida; 80 para permissões e concentração resolvidas sem risco crítico. Mesmo 80 não significa seguro.
- Concentração: `100-top10_pct`, somente quando resolvida; ausente é `null` e contribui zero.
- Total: mercado × 0,35 + segurança × 0,35 + momentum × 0,20 + concentração × 0,10.
- Social não recebe score automático. Smart money é contagem heurística de carteiras observadas, apenas contexto. Nenhum dos dois supera rejeições.

Aceleração das compras é `compras5m*12/compras1h`, quando o denominador está disponível. Não demonstra causalidade, compradores únicos ou tendência sustentável.

## Dinheiro e persistência

Capital inicial: USD 10.000 simulados. Valores monetários usam Decimal/SQL Numeric. Preços externos/scoring usam floats finitos validados por Pydantic. A simulação não movimenta USDC ou SOL.

Entrada: orçamento inclui taxa percentual e custo fixo. Quantidade = `(orçamento-taxas)/(preço*(1+slippage))`.
Saída: proceeds = `quantidade*preço*(1-slippage) - taxa - custo fixo`, com piso zero.
Slippage heurístico = maior entre 20 bps e `valor/(liquidez/2)*10000`. Não é um modelo validado de AMM.
P&L encerrado = proceeds − orçamento. Patrimônio aberto = caixa + quantidade × marcação. Custos futuros de saída não são descontados da marcação aberta. Sem preço novo, a marcação permanece antiga e o painel sinaliza incerteza.

SQLite usa WAL e `BEGIN IMMEDIATE`; PostgreSQL usa bloqueio `FOR UPDATE` na conta. `active_slot=1` tem restrição única e impede uma segunda posição; fechadas ficam com NULL. Order.key é chave primária e persiste a idempotência entre reinícios. Sem chave de ordem nova por tentativa de reenvio. Ordens rejeitadas também ficam registradas.

O limite diário usa patrimônio, incluindo posições abertas, e base do dia UTC. No primeiro evento do novo dia, a referência avança; a trava de emergência exige revisão explícita. Não existe reset de perdas na interface. A reconciliação verifica caixa inicial + P&L encerrado − orçamentos abertos. Divergência impede reativação.

## Agendamento e degradação

Quatro loops independentes: discovery, risco, carteiras e notificações opcionais. Não sobrepõem sua própria execução. Pausa da descoberta não bloqueia risco. Falhas no scanner são eventos e não autorizam entradas. Preço indisponível mantém a posição como `exit_pending`; a próxima consulta tenta novamente sem abrir uma posição concorrente.

Cada provedor tem sua fila e orçamento. Requisições de preço de posições e rotas têm prioridade superior à descoberta pendente; uma requisição já iniciada não é interrompida. HTTP com timeout, até 3 tentativas, backoff e circuito após 3 chamadas fracassadas. Respostas 401/403 não são marteladas com retries imediatos. `Retry-After` numérico e reset epoch são respeitados; caso não interpretável aplica-se recuo conservador. Cache guarda o instante original, sem renovação artificial da idade.

Preços têm idade conservadora calculada como tempo desde a coleta + 60 segundos de possível cache do fornecedor. `source_updated_at=null` indica que não há timestamp verificável do último negócio. Isso é frescor da observação, não prova de frescor on-chain.

## Segurança e acesso

Nenhum campo de chave privada, seed ou assinatura existe na UI. Não há broadcast em nenhuma configuração. Integrações configuradas só por ambiente no backend. Arquivos de ambiente, bancos, logs e keypairs são excluídos do Git.

Local padrão vincula 127.0.0.1. Container expõe somente porta local no compose. Para URLs compartilhadas, use token forte, TLS e controle de acesso do host; a implantação não fornece um serviço de autenticação multiusuário. WebSocket autentica o primeiro frame. APIs mutáveis verificam origem de navegador. Links sociais exigem HTTPS e abrem com noreferrer.

Telegram envia somente após configuração explícita de bot/chat. O cursor evita duplicação normal entre ciclos, mas não oferece exactly-once diante de falha entre envio e commit. Nenhuma mensagem foi enviada durante o desenvolvimento.

## Extensão futura de live

`execution/live.py` define consulta roundtrip, comparação de saldo raw, verificação de confirmação finalizada e protocolo Signer. `submit` sempre falha com PermissionError. Antes de uma futura ativação seriam necessários: signer isolado, inspeção das instruções recebidas, simulação on-chain, allowlist de programas, blockhash/TTL, armazenamento de assinatura e estado submitted/pending/finalized, verificação de saldo por mint, rejeição de extensão não auditada, autorização explícita e testes de recuperação. Esta versão não atende esses requisitos de operação real e não permite ligar live com uma variável.
