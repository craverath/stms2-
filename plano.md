# Plano de implementação do MVP do STMS

## 1. Objetivo e resultado esperado

Construir o STMS como uma aplicação CLI local, instalável em Python 3.12 ou
superior, que orquestre o ciclo completo de uma demanda de desenvolvimento:
preflight, entrevista e aprovação do plano, implementação de uma task DAG em
worktrees isoladas, testes determinísticos, até quatro rodadas condicionais de
revisão, aprovação humana final e integração por um único commit squash.

O resultado esperado é um MVP executável em macOS e Linux no qual:

- `stms start "<demanda>"` e `stms start --file <arquivo>` iniciem um run apenas
  em um repositório Git válido e apto;
- `stms resume [run-id]` retome o último checkpoint seguro, sem depender da
  continuidade da sessão do provedor;
- `plan.md`, `context.md`, checkpoints SQLite, snapshot JSON, logs e eventos
  JSONL tornem cada run rastreável;
- agentes tomem decisões semânticas, mas não controlem Git, transições de
  estado, execução de testes, retries nem políticas de revisão;
- tasks independentes possam executar em paralelo, sem alterar o checkout ou a
  branch original antes do aceite final;
- Codex e Claude Code sejam harnesses suportados, e Pi seja experimental;
- todas as operações relevantes sejam reproduzíveis, auditáveis, limitadas por
  permissões e recuperáveis após interrupção.

## 2. Inventário inicial do repositório

O repositório está em estado greenfield. Na elaboração deste plano foram
encontrados somente:

- `PRD.md`, fonte normativa dos requisitos do MVP;
- `contexto/evidencias-e-metodos.md`, fonte das recomendações de gates humanos,
  lotes pequenos, contexto preciso, versionamento e medição sistêmica;
- metadados de um repositório Git ainda sem commit inicial;
- nenhum código, manifesto, configuração de testes ou convenção adicional de
  implementação.

Consequências para a implementação:

- a estrutura, o empacotamento e a suíte de testes devem ser criados do zero;
- o desenvolvimento do próprio STMS precisará de um commit-base antes de testar
  o produto no repositório real, mas os testes automatizados devem criar
  repositórios Git temporários independentes e não presumir histórico neste
  checkout;
- nenhum implementador deve criar commits, fazer push, merge, rebase ou tag no
  repositório de desenvolvimento; somente o código do produto, quando executado
  em fixtures temporárias, poderá exercitar essas operações;
- `PRD.md` e `contexto/` estão não rastreados no estado observado e devem ser
  preservados.

## 3. Escopo do MVP

### 3.1 Incluído

- Pacote Python 3.12+ com layout `src`, metadados de licença MIT, lockfile do
  `uv` e entrypoint `stms`.
- CLI interativa com Typer, `prompt_toolkit` e Rich, incluindo comportamento
  adequado fora de TTY e controle seguro de `Ctrl-C`.
- Validação versionada de `stms.yml`, sem criação automática do arquivo.
- Preflight completo de Git, configuração, harness, modelo/effort, autenticação,
  sandbox, comandos de teste, exclusividade do run e identidade Git.
- Entrevista multi-turno, limite/gate após dez turnos sem plano, projeções
  `plan.md` e `context.md`, aprovação, feedback e aborto.
- Plano estruturado e versionado, validado por Pydantic, contendo uma DAG de
  tasks e todas as decisões congeláveis.
- Domínio, máquina de estados, políticas de retry e revisão, scheduler por ondas
  e orquestração desacoplados de SDKs, CLI, Git e persistência concreta.
- Persistência operacional em SQLite por meio de LangGraph atrás de
  `WorkflowEngine`, snapshot `state.json` e artefatos locais por run.
- Lock persistente por repositório, checkpoints antes e depois de efeitos
  externos, `operation_id` e reconciliação idempotente.
- Gerenciamento determinístico de branches/worktrees, integração serializada,
  resolução assistida de conflitos, merge squash final e limpeza após sucesso.
- Descoberta e congelamento dos comandos de teste, execução sem shell por
  padrão, timeout com encerramento de filhos e logs completos/redigidos.
- Sandbox Runtime da Anthropic como backend padrão, com políticas por papel,
  validação de capacidades e fallback nativo apenas quando explicitamente
  autorizado.
- Papéis de planejador, implementador e revisor com prompts padrão substituíveis
  e contratos de saída estruturados.
- Adapter comum de harness, adapters Codex e Claude suportados, adapter Pi
  experimental e harness falso para testes determinísticos.
- Normalização de eventos, armazenamento local em `events.jsonl`, redação de
  segredos e ausência de telemetria externa.
- Testes unitários, de integração, E2E com harness falso e testes manuais
  opcionais de conformidade dos adapters reais.
- README e documentação técnica em inglês, exemplo completo de `stms.yml` e guia
  para adicionar agentes/adapters.

### 3.2 Fora do escopo

- Windows, servidor multiusuário, workers distribuídos ou coordenação remota.
- Pull requests, push, CI/CD, deploy e integração com telemetria externa.
- Dashboard, marketplace de plugins ou workflow inteiramente configurável em
  YAML.
- Migração automática de schemas incompatíveis.
- Publicação obrigatória no PyPI.
- Metas de cobertura, geração indiscriminada de testes ou testes de áreas não
  afetadas pela demanda.
- Lint, typecheck e build como gates obrigatórios do workflow atendido.
- Instalação ou autenticação automática de harnesses e criação automática de
  ambiente virtual para o projeto atendido.
- Instalação de dependências não declaradas no plano aprovado.
- Persistência de raciocínio interno do modelo, payloads brutos de provedores,
  tokens, credenciais ou valores de variáveis sensíveis.
- Fallback silencioso de modelo, effort, harness, sandbox ou output estruturado.

## 4. Decisões e premissas

### 4.1 Decisões derivadas do PRD

1. **Núcleo orientado a contratos.** O domínio dependerá de `Protocol`s e tipos
   Pydantic explícitos. SDKs, LangGraph, SQLite, subprocessos, terminal e Git
   ficarão em adapters ou componentes determinísticos.
2. **Orquestração assíncrona.** A API de aplicação será assíncrona para permitir
   streaming de harnesses, prompt interativo e execução concorrente de tasks,
   mantendo funções puras nas políticas e no domínio.
3. **SQLite como verdade operacional.** `state.json`, Markdown, JSONL e logs são
   projeções/referências. Nunca serão usados isoladamente para decidir uma
   transição.
4. **Artefatos atômicos.** Snapshots e projeções serão escritos em arquivo
   temporário no mesmo diretório e substituídos atomicamente. Eventos JSONL
   terão append serializado e flush antes do checkpoint posterior.
5. **Git centralizado.** Somente `WorktreeManager` poderá executar mutações Git.
   Agentes receberão políticas que neguem stage, commit, merge, rebase e push.
6. **Processos sem shell.** Comandos serão listas de argumentos. `shell: true`
   exigirá configuração e aprovação explícitas, será representado no schema e
   nunca decorrerá da simples presença de metacaracteres.
7. **Falha fechada.** Capacidades ausentes, configuração incompatível, sandbox
   inseguro, schema incompatível ou output inválido não serão degradados
   silenciosamente.
8. **Testes observáveis.** Apenas exit code, timeout/sinal e política configurada
   definirão sucesso; mensagens do agente não poderão marcar teste como aprovado.
9. **Contexto mínimo por papel.** Cada sessão receberá apenas os artefatos,
   estado, diff, logs e codebase necessários para a função corrente.
10. **Documentação em inglês.** README, exemplos e documentação técnica serão em
    inglês; a arquitetura manterá mensagens da CLI separáveis para futura
    localização.

### 4.2 Resoluções conservadoras para ambiguidades

- **`PREFLIGHT` não aparece na lista de estados persistidos.** Ele será uma fase
  anterior à criação do run. Falha de preflight não cria um run ativo; pode
  emitir apenas diagnóstico local. A criação do diretório/registro do run e a
  aquisição definitiva do lock ocorrerão somente depois do preflight completo.
- **`HUMAN_ESCALATION` aparece no fluxo, mas não na lista de estados.** Será
  representado como `PAUSED` com `phase=REVIEWING`, subfase
  `HUMAN_ESCALATION`, motivo e próximos eventos explícitos. Não será criado um
  estado principal adicional sem revisão do PRD.
- **Aborto em gates diferentes.** Aborto definitivo produzirá `FAILED` com
  motivo tipado `USER_ABORTED`, preservará os artefatos e liberará recursos
  ativos; pausa continuará retomável e não será tratada como falha.
- **Branch base alterada antes do merge.** O run irá para `PAUSED` sem rebase ou
  merge automático. A continuação só poderá ocorrer após uma decisão humana
  explicitamente suportada; o MVP não inventará uma estratégia automática de
  atualização da base.
- **Integração de uma onda.** Todas as worktrees da onda nascerão do mesmo HEAD
  da branch de integração. Após testes focados, `WorktreeManager` criará commits
  temporários e os aplicará um a um na integração. Uma task dependente só será
  liberada depois que toda a onda anterior necessária estiver integrada e a
  suíte completa tiver sido aprovada.
- **Conflito de integração.** A tentativa com conflito será abortada de forma
  determinística e o implementador receberá uma worktree atualizada a partir da
  integração, com a task, o diff anterior e os detalhes do conflito. Não haverá
  resolução automática opaca.
- **Testes focados sem comando próprio.** O plano estruturado de cada task deverá
  apontar subconjuntos dos comandos congelados ou argumentos/filtros aprovados.
  Se isso não for possível, executa-se o comando completo; nunca se declara
  sucesso sem execução.
- **Projeto atendido sem testes prévios.** As tasks ainda devem criar testes
  essenciais. A suíte completa será o conjunto de comandos aprovados que passa a
  incluir esses testes; ausência real de qualquer comando executável deve voltar
  ao planejamento, não virar aprovação automática.
- **Adapters reais e nomes de pacotes.** As versões e APIs dos SDKs devem ser
  verificadas na documentação oficial durante a implementação. O plano não
  presume nomes de distribuição não confirmados. Dependências de harness podem
  ficar em extras isolados, mas a instalação completa usada para aceite deve
  oferecer Codex e Claude e registrar as versões efetivas.
- **LangGraph.** Apenas o adapter de `WorkflowEngine` e seu checkpoint store
  podem importar LangGraph. A máquina de estados e as regras permanecem
  testáveis sem ele.
- **Pi.** Será marcado experimental na configuração, documentação e eventos. Seu
  RPC JSONL poderá reparar saída até duas vezes, mas não poderá contornar o
  sandbox ou aceitar texto livre como contrato válido.
- **Limpeza de recursos.** Worktrees e branches temporárias serão removidas
  somente após merge final confirmado. Em pausa/falha, são preservadas quando
  necessárias à retomada; operações parcialmente concluídas serão reconciliadas
  por `operation_id`.
- **`.gitignore`.** A entrada `/.stms/estado/` será preparada na branch de
  integração como parte da mudança final, sem tocar a branch original antes da
  aprovação. A própria configuração e prompts versionáveis sob `.stms/` não
  serão ignorados por uma regra ampla.

### 4.3 Dependências de execução propostas

As dependências exatas e seus pins serão registrados em `pyproject.toml` e
`uv.lock` após validação das APIs oficiais:

- Typer para comandos e validação da CLI;
- `prompt_toolkit` para entrada conversacional assíncrona;
- Rich para Markdown, status, progresso e relatórios;
- Pydantic 2 para contratos e schemas versionados;
- parser YAML seguro para `stms.yml`;
- LangGraph e backend SQLite compatível para checkpoints;
- SDK oficial do Codex como transporte principal do adapter Codex;
- Claude Agent SDK para Python;
- bibliotecas padrão para `asyncio`, SQLite, JSON, subprocessos, filesystem,
  hashing e sinais sempre que suficientes;
- pytest e plugins mínimos realmente necessários aos testes assíncronos e de
  timeout.

O Sandbox Runtime e o executável Pi são capacidades externas verificadas pelo
preflight, não devem ser instalados automaticamente pelo STMS.

## 5. Arquitetura proposta

### 5.1 Camadas e direção das dependências

```text
CLI / composição
      |
      v
application (casos de uso e orquestração)
      |
      v
domain (modelos, estados, políticas, eventos e portas)
      ^
      |
adapters + deterministic (implementações das portas)
```

- `domain` não importa CLI, LangGraph, SQLite, SDKs, Git nem subprocessos.
- `application` coordena portas e políticas; não executa comandos diretamente.
- `deterministic` contém processos, testes e Git, sem decisões semânticas de
  agente.
- `adapters` traduz fornecedores, persistência, sandbox e terminal para os
  contratos internos.
- `composition.py` é o único ponto que escolhe implementações concretas.

### 5.2 Modelo de domínio mínimo

Os schemas versionados devem incluir, sem se limitar a:

- `RunId`, `TaskId`, `OperationId`, `SessionId`, `AttemptId` e `ReviewRound`;
- `RunState`, `RunPhase`, `RunSubphase`, `Transition`, `AllowedEvent` e
  `PauseReason`;
- `ApprovedPlan`, `PlanTask`, `TaskDependency`, `ExecutionMode`,
  `AcceptanceCriterion`, `TestCommand` e `ApprovedUntrackedFile`;
- `RuntimeConfig`, configurações por agente, workflow, testes, revisão e
  segurança;
- `HarnessRequest`, `HarnessResult`, `NormalizedHarnessEvent`, uso e erro;
- outputs de planner, implementer e reviewer;
- `TestAttempt`, `ProcessResult`, `IntegrationResult`, `ReviewFinding` e decisão
  do gate;
- `Checkpoint`, metadados de versão/digest e registro de operação externa;
- eventos operacionais normalizados com referências, nunca segredos.

Todos os schemas persistidos terão `schema_version`. Enums serão serializados de
forma estável e erros públicos incluirão uma ação corretiva sem expor dados
sensíveis.

### 5.3 Workflow e checkpoints

`WorkflowEngine` oferecerá comandos de domínio explícitos em vez de uma API
genérica de alteração de estado. Cada fronteira externa seguirá:

1. validar estado e evento permitido;
2. criar/recuperar `operation_id` determinístico;
3. persistir checkpoint `pending`;
4. executar ou reconciliar o efeito externo;
5. persistir resultado, eventos e referências a artefatos;
6. transicionar atomicamente para o próximo estado;
7. atualizar `state.json` somente após o checkpoint confirmado.

A retomada deve distinguir operação não iniciada, pendente, confirmada e
reconciliável. Operações confirmadas não podem ser repetidas.

### 5.4 Scheduler e integração

- Validar a DAG antes da aprovação: IDs únicos, dependências existentes, ausência
  de ciclos, pelo menos uma task e critérios/testes por task.
- Calcular ondas topológicas determinísticas, com desempate estável por ordem do
  plano.
- Executar no máximo `max_parallel_tasks` sessões/worktrees simultâneas.
- Isolar falha por task, cancelar com segurança quando a política exigir e
  registrar todos os resultados.
- Testar cada task na própria worktree antes de criar o commit temporário.
- Integrar resultados aprovados serialmente e executar a suíte completa na
  branch de integração.
- Liberar dependentes apenas após confirmação de integração e testes da onda.

### 5.5 Segurança

- Gerar políticas de sandbox fora da worktree e validá-las antes da sessão.
- Aplicar filesystem, rede e Git conforme o papel definido no PRD.
- Negar rede do TestRunner por padrão; autorizar instalação somente conforme
  dependências/domínios congelados no plano.
- Passar segredos por referência, nunca copiá-los para prompt, checkpoint,
  comando renderizado ou log.
- Redigir chaves sensíveis tanto em nomes de variáveis quanto em conteúdo
  detectável, preservando indicação de que houve redação.
- Validar paths com resolução canônica, impedir traversal e symlinks que escapem
  do repositório e limitar tamanho de arquivos não rastreados.
- Renderizar texto de agentes sem interpretar markup externo ou sequências de
  controle do terminal.

## 6. Estrutura de arquivos proposta

```text
.
├── pyproject.toml
├── uv.lock
├── LICENSE
├── README.md
├── stms.example.yml
├── docs/
│   ├── architecture.md
│   ├── adapters.md
│   └── harness-conformance.md
├── src/stms/
│   ├── __init__.py
│   ├── composition.py
│   ├── cli/
│   │   ├── app.py
│   │   ├── interaction.py
│   │   ├── renderer.py
│   │   └── exit_codes.py
│   ├── agents/
│   │   ├── planner.py
│   │   ├── implementer.py
│   │   └── reviewer.py
│   ├── application/
│   │   ├── orchestrator.py
│   │   ├── workflow.py
│   │   ├── scheduler.py
│   │   ├── preflight.py
│   │   └── services.py
│   ├── domain/
│   │   ├── errors.py
│   │   ├── models.py
│   │   ├── states.py
│   │   ├── policies.py
│   │   ├── events.py
│   │   └── ports.py
│   ├── deterministic/
│   │   ├── process_runner.py
│   │   ├── test_discovery.py
│   │   ├── test_runner.py
│   │   └── worktree_manager.py
│   └── adapters/
│       ├── harnesses/
│       │   ├── base.py
│       │   ├── codex.py
│       │   ├── claude.py
│       │   ├── pi.py
│       │   └── fake.py
│       ├── persistence/
│       │   ├── langgraph_engine.py
│       │   ├── sqlite_store.py
│       │   └── artifact_store.py
│       ├── sandbox/
│       │   ├── policy.py
│       │   ├── srt.py
│       │   └── native.py
│       └── terminal/
│           ├── prompt.py
│           └── rich_renderer.py
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── conformance/
    └── fixtures/
```

Os nomes podem ser refinados para evitar módulos artificiais, desde que as
fronteiras e responsabilidades permaneçam. Arquivos pequenos demais podem ser
combinados; módulos de domínio, adapters de fornecedor e componentes
determinísticos não devem ser misturados.

## 7. Ordem de execução e dependências

```text
T01
 ├─> T02 ─> T03 ─┬─> T06 ─> T07
 │                ├─> T08
 │                ├─> T09
 │                └─> T10
 └─> T04 ─> T05 ──────────┘

T03 + T04 ─> T11 ─> T12 ─┬─> T13 (Codex)
                          ├─> T14 (Claude)
                          └─> T15 (Pi, após T10)

T06..T12 ─> T16 ─> T17 ─> T18
T13 + T14 + T15 + T18 ─> T19 ─> T20
```

- T02 e T04 podem avançar em paralelo após o scaffold.
- T06, T08, T09 e T10 podem ser implementadas em paralelo após estabilizar as
  portas e contratos correspondentes.
- T13 e T14 podem avançar em paralelo; T15 também pode, mas depende do contrato
  de sandbox e RPC.
- T16 a T20 formam o caminho de integração e devem ser sequenciais para reduzir
  retrabalho.

## 8. Tarefas de implementação

### T01 — Scaffold, empacotamento e convenções

**Dependências:** nenhuma. **Execução:** sequencial.

**Implementação:**

- criar o layout `src/stms`, diretórios de testes e arquivos de documentação;
- configurar Python 3.12+, entrypoint `stms`, dependências e grupos/extras no
  `pyproject.toml`;
- gerar `uv.lock`, adicionar licença MIT e configuração mínima de pytest;
- manter adapters reais isolados para que testes com harness falso não exijam
  autenticação;
- não adicionar lint, typecheck ou cobertura como gates obrigatórios.

**Áreas prováveis:** `pyproject.toml`, `uv.lock`, `LICENSE`, `src/stms/__init__.py`,
`tests/`.

**Critérios de aceite:** o pacote importa em Python 3.12+, o entrypoint resolve e
a suíte vazia/mínima pode ser coletada sem carregar SDKs opcionais nem acessar a
rede.

**Testes essenciais:** smoke test de import e invocação de `stms --help`.

### T02 — Modelos, schemas e erros de domínio

**Dependências:** T01. **Execução:** paralela com T04.

**Implementação:**

- definir IDs tipados, enums, schemas versionados e modelos Pydantic listados na
  seção 5.2;
- criar outputs estritos para planner, implementer e reviewer;
- modelar plano, DAG, comandos, permissões congeladas, arquivos não rastreados e
  metadados de compatibilidade;
- definir hierarquia de erros acionáveis, separando configuração, domínio,
  infraestrutura, segurança e incompatibilidade.

**Áreas prováveis:** `domain/models.py`, `domain/events.py`, `domain/errors.py`.

**Critérios de aceite:** contratos válidos serializam de forma estável; campos
desconhecidos relevantes são rejeitados; versões e IDs obrigatórios não podem
ser omitidos; valores sensíveis não integram representações públicas.

**Testes essenciais:** round-trip dos schemas, rejeição de DAG/config/resultados
malformados e compatibilidade de enums persistidos.

### T03 — Portas e contratos substituíveis

**Dependências:** T02. **Execução:** sequencial antes dos adapters.

**Implementação:**

- definir `AgentHarness`, `WorkflowEngine`, `CheckpointStore`, `ArtifactStore`,
  `WorktreeManager`, `TestRunner`, `SandboxRuntime`, `EventSink`,
  `PromptProvider`, `PromptPort` e `EventRenderer` como protocolos explícitos;
- incluir início/retomada/cancelamento, streaming normalizado, cwd absoluto,
  modelo/effort, ferramentas, timeout, turnos e output estruturado no harness;
- representar capacidades e versões no preflight;
- evitar métodos genéricos que permitam furar as regras do domínio.

**Áreas prováveis:** `domain/ports.py`, `adapters/harnesses/base.py`.

**Critérios de aceite:** fakes mínimos implementam cada porta sem importar
infraestrutura concreta; metadados específicos de fornecedor não vazam nos
casos de uso.

**Testes essenciais:** checks estruturais/contratuais dos fakes e normalização de
erros públicos.

### T04 — Configuração versionada e descoberta de testes

**Dependências:** T01. **Execução:** paralela com T02.

**Implementação:**

- carregar `stms.yml` com parser seguro e validar `version: 1`, seções, limites,
  severidades, retries, timeout, modelo/effort e política de sandbox;
- recusar chaves/valores inválidos com caminho e correção sugerida;
- calcular digest canônico sem segredos;
- descobrir comandos na precedência do PRD: configuração, documentação/CI,
  manifestos e proposta do planejador;
- representar cada comando como argv, cwd relativo validado, timeout e opção de
  shell explicitamente aprovada;
- congelar o conjunto aprovado no checkpoint.

**Áreas prováveis:** `domain/models.py`, `deterministic/test_discovery.py`,
`application/preflight.py`, `stms.example.yml`.

**Critérios de aceite:** ausência de arquivo produz exemplo completo sem criar
arquivo; versão desconhecida falha; nenhuma detecção executa comando; mudança da
configuração após aprovação é detectada pelo digest.

**Testes essenciais:** configurações válida/inválidas, precedência, manifestos
comuns, shell negado por padrão e digest estável.

### T05 — Máquina de estados, DAG, políticas e retries

**Dependências:** T02 e T04. **Execução:** sequencial.

**Implementação:**

- codificar estados principais e auxiliares, fases, subfases e eventos válidos;
- rejeitar transição antes de qualquer efeito externo;
- validar DAG e produzir ondas topológicas determinísticas;
- codificar exatamente os limiares das quatro revisões;
- separar retries de infraestrutura, output estruturado, implementação, testes e
  conflito, respeitando os limites do PRD;
- garantir que retry de infraestrutura não avance rodada nem conte como falha de
  implementação;
- modelar gate após dez turnos do planejador sem plano.

**Áreas prováveis:** `domain/states.py`, `domain/policies.py`,
`application/scheduler.py`.

**Critérios de aceite:** toda transição do fluxo funcional é representada; toda
transição não permitida falha sem side effect; a revisão bloqueia por
`high/medium/low`, `high/medium`, `high` e nenhuma severidade nas rodadas 1–4,
escalando `high` na quarta.

**Testes essenciais:** tabela completa de transições, DAG cíclica/inválida,
ondas e concorrência, quatro rodadas e limites de retries.

### T06 — Artefatos, eventos e proteção de segredos

**Dependências:** T03 e T05. **Execução:** paralela com T08–T10.

**Implementação:**

- criar a árvore `.stms/estado/<run-id>` e stores para Markdown, JSON, JSONL,
  logs e revisões;
- escrever `plan.md`, `context.md` e `state.json` atomicamente;
- preservar somente contexto útil, nunca a transcrição completa;
- emitir eventos mínimos e campos operacionais do PRD;
- redigir segredos, desabilitar payload bruto por padrão e registrar truncamento;
- validar e copiar somente arquivos não rastreados explicitamente aprovados,
  respeitando tamanho, padrões sensíveis, symlink e destino.

**Áreas prováveis:** `adapters/persistence/artifact_store.py`,
`domain/events.py`, utilitários de segurança.

**Critérios de aceite:** estrutura por run corresponde ao PRD; snapshots nunca
ficam parcialmente gravados; evento não controla transição; segredos e arquivos
não aprovados não chegam aos artefatos/worktrees.

**Testes essenciais:** atomicidade simulando interrupção, append concorrente,
redação, truncamento, traversal/symlink e allowlist de arquivos não rastreados.

### T07 — Checkpoints SQLite, LangGraph, lock e retomada

**Dependências:** T05 e T06. **Execução:** sequencial.

**Implementação:**

- implementar SQLite como fonte operacional e LangGraph atrás de
  `WorkflowEngine`;
- persistir versões/digests, estado completo, próximos eventos e operações
  externas;
- adquirir lock por repositório com PID, run-id e timestamp;
- detectar lock vivo, lock órfão e múltiplos runs ativos;
- selecionar o run retomável mais recente quando o ID for omitido;
- verificar compatibilidade de schema/workflow/config/prompts/adapters no resume;
- reconciliar operações pendentes e não repetir operações confirmadas;
- liberar lock em conclusão/aborto e preservar estado do run.

**Áreas prováveis:** `adapters/persistence/langgraph_engine.py`,
`adapters/persistence/sqlite_store.py`, `application/workflow.py`.

**Critérios de aceite:** interrupção em ambos os lados de uma fronteira externa
retoma de forma segura; schema incompatível falha com diagnóstico; sessão perdida
pode ser substituída; somente um run fica ativo por repositório.

**Testes essenciais:** checkpoints before/after, crash injection, lock órfão,
concorrência de lock, seleção do run e idempotência por `operation_id`.

### T08 — Process runner e TestRunner determinístico

**Dependências:** T03 e T05. **Execução:** paralela com T06, T09 e T10.

**Implementação:**

- executar argv sem shell por padrão, com cwd canônico e ambiente allowlisted;
- capturar stdout/stderr, duração, exit code/sinal, timeout e truncamento;
- em timeout/cancelamento, encerrar a árvore de processos em macOS e Linux;
- implementar testes focados por task e suíte completa na integração;
- persistir log por tentativa e devolver evidência estruturada;
- negar rede conforme política do sandbox e não confiar em afirmação do agente.

**Áreas prováveis:** `deterministic/process_runner.py`,
`deterministic/test_runner.py`.

**Critérios de aceite:** timeout padrão de teste é 900 segundos; resultado deriva
somente do processo; nenhum metacaractere ativa shell; processos filhos não
sobrevivem ao timeout.

**Testes essenciais:** sucesso/falha, stdout/stderr, ambiente, cwd, truncamento,
timeout com filho, cancelamento e shell explicitamente permitido/negado.

### T09 — WorktreeManager e operações Git

**Dependências:** T03 e T05. **Execução:** paralela com T06, T08 e T10.

**Implementação:**

- encapsular comandos Git com argv e erros acionáveis;
- criar branches `stms/<run-id>/integration` e
  `stms/<run-id>/task-<task-id>` no commit-base registrado;
- criar worktrees por onda a partir do mesmo commit da integração;
- stage/commit temporário somente após task aprovada, usando identidade validada;
- integrar serialmente, detectar conflito e abortar tentativa incompleta;
- comparar branch-base/commit-base antes do merge;
- produzir um único commit squash com mensagem padrão e integrar na branch
  original somente após aprovação;
- remover worktrees/branches temporárias apenas após sucesso confirmado;
- tornar as operações reconciliáveis por inspeção de refs, commits e worktrees.

**Áreas prováveis:** `deterministic/worktree_manager.py`.

**Critérios de aceite:** branch original não muda antes do gate final; tasks da
onda partem do mesmo commit; dependentes partem do estado integrado; mudança da
base pausa; sucesso deixa exatamente um commit novo e remove recursos temporários.

**Testes essenciais:** repositórios temporários para criação, integração,
conflito, três falhas, squash, base alterada, limpeza e retomada após cada efeito.

### T10 — Sandbox e políticas por papel

**Dependências:** T03 e T04. **Execução:** paralela com T06, T08 e T09.

**Implementação:**

- traduzir políticas do domínio para configuração do Sandbox Runtime;
- validar executável, versão e capacidades efetivas no preflight;
- aplicar perfis de planner, implementer, reviewer e TestRunner conforme o PRD;
- implementar fallback para sandbox nativo apenas se permitido e capaz de
  satisfazer integralmente a política;
- gerar arquivos de política fora das worktrees e removê-los com segurança;
- autorizar rede temporária de instalação apenas para domínios aprovados.

**Áreas prováveis:** `adapters/sandbox/policy.py`, `srt.py`, `native.py`.

**Critérios de aceite:** indisponibilidade/incompatibilidade falha fechada;
implementador escreve só na worktree; planner/reviewer não escrevem; TestRunner
fica sem rede por padrão; fallback nunca é silencioso.

**Testes essenciais:** geração de cada perfil, capability mismatch, fallback
negado/permitido e tentativa de acesso fora de filesystem/rede autorizados com
backend falso.

### T11 — Papéis de agente e prompts substituíveis

**Dependências:** T03, T04 e T05. **Execução:** sequencial antes dos adapters.

**Implementação:**

- implementar planner, implementer e reviewer com uma responsabilidade cada;
- manter prompt padrão no módulo do papel e permitir override por
  `PromptProvider`/caminho validado;
- limitar perguntas do planner a grupos de até três, produzir `needs_input` ou
  plano válido e registrar fontes web relevantes em `context.md`;
- exigir do implementer testes essenciais primeiro, escopo congelado e relatório
  estruturado;
- exigir do reviewer findings com ID, severidade, evidência, localização e
  correção sugerida;
- reparar output inválido no máximo duas vezes e então pausar.

**Áreas prováveis:** `agents/planner.py`, `implementer.py`, `reviewer.py`.

**Critérios de aceite:** agentes não importam CLI, Git, SQLite ou LangGraph; não
podem aprovar o próprio plano, executar testes ou mudar a política; cada contrato
inválido segue exatamente a política de reparo.

**Testes essenciais:** prompts/entradas mínimas por papel, limite de perguntas,
outputs válidos, duas reparações e descoberta material que exige novo gate.

### T12 — Harness falso e suíte de contrato comum

**Dependências:** T03 e T11. **Execução:** sequencial antes da orquestração.

**Implementação:**

- criar harness scriptável que emita eventos, respostas, falhas, timeouts,
  cancelamentos, perda de sessão e uso;
- criar suíte compartilhada para início/retomada, cwd, modelo/effort, ferramentas,
  streaming, limites e output estruturado;
- garantir que nova sessão possa ser hidratada pelo estado persistido.

**Áreas prováveis:** `adapters/harnesses/fake.py`, `tests/fixtures/`,
`tests/conformance/`.

**Critérios de aceite:** todos os casos de uso podem ser testados offline e de
forma determinística; a suíte de contrato pode ser reutilizada pelos três
adapters reais.

**Testes essenciais:** sequência de eventos, cancelamento, timeout, retry de
infraestrutura, perda de sessão e contabilidade opcional de uso.

### T13 — Adapter Codex

**Dependências:** T10 e T12. **Execução:** paralela com T14 e T15.

**Implementação:**

- confirmar API e versão do Codex SDK oficial e usá-lo como transporte principal;
- mapear sessão, retomada, streaming, cancelamento, cwd, modelo/effort,
  ferramentas, timeout e JSON estruturado;
- validar autenticação e capacidades no preflight;
- normalizar eventos e uso sem persistir payload bruto/raciocínio interno;
- traduzir sandbox e erros do provedor para tipos de domínio.

**Áreas prováveis:** `adapters/harnesses/codex.py`, dependências/extras e testes
de conformidade.

**Critérios de aceite:** passa a suíte comum com transporte simulado; configuração
incompatível falha sem downgrade; teste manual documentado cobre autenticação,
sessão, streaming, cancelamento, output e sandbox.

**Testes essenciais:** unitários com SDK mockado na fronteira e conformidade real
manual, marcada fora da suíte automática.

### T14 — Adapter Claude Code

**Dependências:** T10 e T12. **Execução:** paralela com T13 e T15.

**Implementação:**

- integrar o Claude Agent SDK para Python;
- normalizar diferenças de sessão, permissões, effort, eventos e output;
- validar instalação, autenticação, modelo e capacidades no preflight;
- aplicar a mesma política de logs, timeout, cancelamento e reparo estruturado.

**Áreas prováveis:** `adapters/harnesses/claude.py`, dependências/extras e testes
de conformidade.

**Critérios de aceite:** passa a suíte comum com SDK mockado; ausência de
capacidade requerida falha de forma acionável; não há permissões mais amplas que
as do papel.

**Testes essenciais:** unitários de tradução/stream/cancelamento e conformidade
real manual fora dos gates automáticos.

### T15 — Adapter Pi experimental

**Dependências:** T10 e T12. **Execução:** paralela com T13 e T14.

**Implementação:**

- iniciar Pi como subprocesso com RPC JSONL bidirecional;
- correlacionar mensagens, eventos, cancelamento, timeout e encerramento;
- validar e reparar outputs Pydantic até duas vezes;
- compor com `SandboxRuntime`, sem alegar sandbox/output nativos inexistentes;
- expor status experimental em configuração, evento e documentação.

**Áreas prováveis:** `adapters/harnesses/pi.py`, testes de protocolo.

**Critérios de aceite:** framing parcial/múltiplo é tratado; resposta fora do
schema nunca é aceita como texto livre; processo filho é encerrado no
cancelamento; limitações são explícitas.

**Testes essenciais:** servidor RPC falso, mensagens fragmentadas, erro de JSON,
duas reparações, timeout, crash e conformidade manual opcional.

### T16 — Preflight e criação segura do run

**Dependências:** T04, T07, T09, T10, T12 e contratos de T13–T15.
**Execução:** sequencial.

**Implementação:**

- verificar cwd igual à raiz Git, HEAD e branch selecionada, identidade Git,
  alterações rastreadas, configuração e comandos;
- permitir arquivos não rastreados sem copiá-los automaticamente;
- verificar lock/run ativo, harnesses selecionados, versões, autenticação,
  modelo/effort, sandbox e capacidades;
- agregar diagnósticos seguros e ações corretivas;
- só depois do sucesso criar run, registrar commit/branch-base, versões/digests,
  adquirir lock e entrar em `INTERVIEWING`.

**Áreas prováveis:** `application/preflight.py`, `application/services.py`.

**Critérios de aceite:** cada pré-condição do PRD é validada; falha não cria
commit nem run ativo; untracked não causa rejeição; erro indica como corrigir.

**Testes essenciais:** matriz de falhas de preflight, repo sem commit/detached
HEAD/subdiretório/dirty tracked, untracked permitido, identidade ausente e
capability mismatch.

### T17 — Orquestração de planejamento e aprovação

**Dependências:** T07, T11, T12 e T16. **Execução:** sequencial.

**Implementação:**

- conduzir entrevista assíncrona multi-turno e persistir checkpoints entre
  turnos;
- abrir gate de continuar/reformular/abortar após dez turnos sem plano;
- validar output, DAG e projeções, mantendo entrevista fora dos artefatos;
- permitir aprovar, fornecer novo prompt ou abortar;
- sobrescrever `plan.md`/`context.md` em nova versão sem criar `plan.vN.md`;
- congelar comandos, dependências, arquivos não rastreados e permissões somente
  após aprovação.

**Áreas prováveis:** `application/orchestrator.py`, `application/workflow.py`,
`agents/planner.py`.

**Critérios de aceite:** implementação nunca inicia sem aprovação explícita;
feedback retorna a `INTERVIEWING`; histórico operacional permanece nos
checkpoints/eventos; projeções contêm apenas o conteúdo prescrito.

**Testes essenciais:** entrevista curta/longa, gate do décimo turno, feedback,
aprovação, aborto, overwrite e tentativa de alterar configuração congelada.

### T18 — Implementação, testes, integração e correções

**Dependências:** T07–T12 e T17. **Execução:** sequencial na integração, com
concorrência interna por ondas.

**Implementação:**

- materializar a DAG aprovada e criar sessões/worktrees isoladas por task;
- executar tasks prontas até o limite configurado, todas sobre o mesmo commit da
  onda;
- exigir testes essenciais primeiro e validar resultado estruturado;
- rodar testes focados, criar commit temporário e integrar serialmente;
- devolver conflitos ao implementer com estado integrado e limitar a três
  tentativas;
- rodar suíte completa após integração e devolver falhas com logs/diff completos
  em sessão nova na worktree de integração;
- limitar a três correções por etapa e pausar ao exceder;
- checkpointar/cancelar com segurança em `Ctrl-C`.

**Áreas prováveis:** `application/orchestrator.py`, `scheduler.py`, `services.py`.

**Critérios de aceite:** duas tasks independentes realmente sobrepõem sua
execução; dependente aguarda integração; nenhuma task altera branch original;
falha real volta ao agente e sucesso alegado sem teste é ignorado.

**Testes essenciais:** concorrência controlada, dependência, falha focada,
regressão na suíte completa, três correções, três conflitos, cancelamento e
retomada entre todas as fronteiras.

### T19 — Revisão progressiva, gate final, merge e retomada completa

**Dependências:** T13–T15 e T18. **Execução:** sequencial.

**Implementação:**

- iniciar sessão nova por rodada com objetivo, critérios, plano/contexto, diff,
  testes, findings bloqueantes anteriores e codebase read-only;
- aplicar os quatro limiares exatamente e criar rodada seguinte somente após
  correção exigida;
- não carregar nem mostrar no relatório final findings não bloqueantes;
- após correção, executar suíte completa e incrementar rodada adequada;
- mapear `high` na rodada 4 para pausa/escalação humana;
- implementar gate final: aprovar, ajustar, replanejar ou abortar;
- em ajuste, reiniciar revisão na rodada 1 após testes; em replanejamento,
  sobrescrever projeções e reiniciar o ciclo no mesmo run;
- antes do merge, comparar a base; após aprovação e base intacta, squash, merge,
  confirmar resultado, limpar temporários e marcar `COMPLETED`;
- implementar resume e encerramento definitivo de runs retomáveis.

**Áreas prováveis:** `application/orchestrator.py`, `workflow.py`,
`domain/policies.py`.

**Critérios de aceite:** política de revisão e gates reproduzem o PRD; alteração
da base pausa; merge aprovado cria um commit com autor Git e mensagem
`stms: <resumo> [<run-id>]`; estado e registros permanecem após limpeza.

**Testes essenciais:** cada severidade/rodada, correção e reset, escalação,
ajuste, replanejamento, aborto, base alterada, merge e perda de sessão seguida de
resume.

### T20 — CLI, experiência terminal, documentação e validação final

**Dependências:** T19. **Execução:** sequencial.

**Implementação:**

- expor somente `start` e `resume` como comandos MVP, além da ajuda padrão;
- aceitar exatamente prompt ou `--file`, com validações claras;
- garantir um único controlador da linha de entrada e serializar atualizações
  concorrentes sem corromper o prompt;
- renderizar Markdown, tasks, worktrees, testes, revisões e gates com Rich;
- desabilitar animações fora de TTY e neutralizar sequências/markup externos;
- primeiro `Ctrl-C`: pedir pausa segura e aguardar checkpoint corrente; segundo:
  forçar encerramento com código 130;
- mapear códigos 0, 1, 2, 3 e 130;
- escrever README e documentação técnica em inglês cobrindo os 14 tópicos do
  PRD, limitações e conformidade manual;
- executar a matriz final e corrigir somente falhas dentro do escopo aprovado.

**Áreas prováveis:** `cli/`, `adapters/terminal/`, `composition.py`, `README.md`,
`docs/`, `stms.example.yml`.

**Critérios de aceite:** os 18 critérios do PRD são demonstráveis; instalação
isolada via `uv tool install` expõe `stms`; fluxos críticos funcionam com harness
falso; adapters reais têm passos de conformidade claros sem integrar testes
online à suíte determinística.

**Testes essenciais:** CLI com runner isolado, TTY/non-TTY, arquivo/prompt,
saídas/códigos, dois `Ctrl-C`, dois E2E críticos e smoke de instalação.

## 9. Comandos de teste e validação propostos

Executar primeiro os testes focados da tarefa alterada e só ampliar quando ela
estiver estável. Os comandos abaixo são do desenvolvimento do STMS; comandos de
projetos atendidos serão descobertos, aprovados e congelados por run.

```shell
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/e2e
uv run pytest tests/unit tests/integration tests/e2e
```

Validações adicionais proporcionais:

- `uv run pytest <arquivo>::<teste>` durante uma correção focada;
- testes de integração Git sempre em diretórios temporários, com identidade local
  configurada somente na fixture;
- testes de processos/sinais em macOS e Linux;
- smoke de instalação em diretórios temporários de tools/bin do `uv`, sem alterar
  a instalação global do desenvolvedor;
- testes de conformance reais marcados como manuais/opt-in e nunca exigidos pela
  suíte offline;
- `uv build` apenas como validação de empacotamento na aceitação, não como gate
  imposto a projetos atendidos.

Não adicionar uma meta numérica de cobertura. A suíte deve provar comportamento
observável, contratos públicos, regressões relevantes e fronteiras externas.

## 10. Matriz de validação do fluxo

| Cenário | Evidência esperada |
| --- | --- |
| Preflight inválido | Código 2, ação corretiva, nenhum run/commit criado |
| Plano sem aprovação | Estado `PLAN_PENDING_APPROVAL`, nenhuma worktree de task |
| Tasks independentes | Execução concorrente limitada e mesmo commit inicial da onda |
| Task dependente | Início somente após dependência integrada e validada |
| Falha de teste | Exit code/log persistido e nova sessão de correção |
| Quatro revisões | Bloqueios 3/2/1/0 e escalação de `high` na rodada 4 |
| Ajuste final | Testes completos e revisão reiniciada na rodada 1 |
| Replanejamento | Mesmo run, novas projeções, histórico operacional preservado |
| Interrupção segura | Código 3 ou 130 conforme ação e retomada do checkpoint seguro |
| Sessão perdida | Nova sessão hidratada por estado/plano/contexto |
| Base alterada | `PAUSED`, nenhum merge/rebase automático |
| Aprovação final | Um commit squash, branch original atualizada e temporários removidos |
| Log/evento | Campos mínimos presentes, sem segredo/payload bruto |
| Schema incompatível | Resume recusado com versão e ação corretiva |

## 11. Riscos e mitigação

| Risco | Mitigação planejada |
| --- | --- |
| APIs e formatos dos harnesses mudarem | Adapters estreitos, versões registradas, preflight e suíte de contrato |
| Codex SDK não oferecer uma capacidade presumida | Validar API oficial antes do adapter; falhar acionavelmente, sem trocar de transporte em silêncio |
| Sandbox Runtime beta ou incapaz no ambiente | Porta própria, capability check, fail-closed e fallback nativo somente explícito |
| LangGraph acoplar regras de negócio | Import restrito ao adapter; políticas e transições puras cobertas unitariamente |
| Crash entre Git/processo e checkpoint | Checkpoints before/after, operation IDs e reconciliação do estado externo |
| Corrida entre tasks ou runs | Lock persistente, scheduler limitado, commits serializados e stores com escrita coordenada |
| Conflitos frequentes em ondas | Plano em lotes pequenos, base idêntica por onda, integração serial e limite de três tentativas |
| Loop de teste/revisão | Contadores separados e pausas/gates nos limites exatos |
| Vazamento de segredo | Contexto mínimo, referências, redação central, payload bruto desabilitado e testes adversariais |
| Escape por path/symlink/untracked | Canonicalização, allowlist aprovada, limites e recusa de symlinks externos |
| Processo filho sobreviver ao timeout | Grupos de processo e testes específicos em macOS/Linux |
| Terminal corrompido por concorrência/conteúdo | Um dono do input, fila de renderização e escape de controle/markup |
| Repositório atual sem commit inicial | Fixtures Git temporárias; documentar que uso real exige HEAD/branch válidos |
| Testes reais caros ou flakey | Suíte determinística com fakes; conformidade online manual e opt-in |
| Escopo crescer para CI/deploy/telemetria | Manter apenas eventos locais e portas de extensão sem implementar integrações fora do MVP |

## 12. Checklist final de aceite

### Produto e instalação

- [ ] Python 3.12+ e licença MIT declarados.
- [ ] `pyproject.toml` e `uv.lock` versionáveis e reproduzíveis.
- [ ] Instalação isolada equivalente a `uv tool install` expõe `stms`.
- [ ] README técnico em inglês cobre onboarding e limitações.

### Planejamento e estado

- [ ] `start` aceita prompt ou `--file` somente após preflight completo.
- [ ] Entrevista suporta múltiplos turnos, até três perguntas por grupo e gate no
      décimo turno sem plano.
- [ ] Plano estruturado contém DAG, critérios, riscos, comandos, arquivos,
      dependências e modo de execução.
- [ ] `plan.md` e `context.md` são projeções corretas e substituídas em feedback.
- [ ] Aprovação humana congela comandos, dependências, untracked e permissões.
- [ ] SQLite é a fonte de verdade; snapshot/eventos/artefatos são consistentes.
- [ ] `resume` valida compatibilidade e recupera checkpoint seguro.

### Implementação, Git e testes

- [ ] Duas tasks independentes executam simultaneamente quando o limite permite.
- [ ] Dependências só iniciam após integração validada.
- [ ] Agentes não executam mutações Git nem controlam resultado dos testes.
- [ ] Worktrees/branches seguem os nomes prescritos e isolam a branch original.
- [ ] Testes focados e suíte completa são executados pelo TestRunner.
- [ ] Timeout encerra filhos e registra todos os campos exigidos.
- [ ] Falhas e conflitos retornam ao implementer e respeitam três correções.

### Revisão, gates e merge

- [ ] Política das quatro rodadas segue exatamente os limiares configurados.
- [ ] Toda correção roda a suíte completa novamente.
- [ ] Ajuste final reinicia revisão na rodada 1.
- [ ] `high` na rodada 4 pausa para decisão humana.
- [ ] Replanejamento permanece no mesmo run e preserva histórico operacional.
- [ ] Mudança da branch-base pausa sem rebase/merge silencioso.
- [ ] Aprovação final cria exatamente um commit squash com autor Git correto.
- [ ] Limpeza ocorre somente após sucesso e preserva `.stms/estado/<run-id>`.

### Segurança, UX e extensibilidade

- [ ] Políticas distintas por papel são aplicadas e testadas.
- [ ] Sandbox falha fechado; fallback exige autorização explícita.
- [ ] Segredos, payload bruto e raciocínio interno não são persistidos.
- [ ] Eventos locais contêm os campos mínimos e não controlam transições.
- [ ] `Ctrl-C` seguro, segundo `Ctrl-C`, TTY/non-TTY e códigos de saída funcionam.
- [ ] Codex e Claude passam testes de contrato simulados e têm conformidade manual
      documentada.
- [ ] Pi funciona via RPC JSONL como adapter explicitamente experimental.
- [ ] Um novo agente, harness, persistência, sandbox ou renderer pode ser
      adicionado pela composição sem modificar os agentes existentes.

### Testes finais

- [ ] Unitários de estados, DAG, revisão, retries, config, schemas, segurança,
      comandos e códigos de saída passam.
- [ ] Integrações de Git, subprocessos, SQLite, locks, retomada e artefatos passam.
- [ ] E2E `start -> merge` com harness falso passa.
- [ ] E2E `falha/interrupção -> correção/resume -> merge` passa.
- [ ] Nada depende de rede, credenciais ou modelo real na suíte automática.
- [ ] Todos os 18 critérios de aceite da seção 21 do PRD têm evidência de teste ou
      validação manual registrada.
