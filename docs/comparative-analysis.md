# Análise Comparativa: fedora-nexus vs GitNexus
> Data: Maio 2026 | Propósito: Identificar gaps de performance e assertividade para evolução do fedora-nexus

---

## Sumário Executivo

O fedora-nexus e o GitNexus resolvem o mesmo problema: construir grafos de dependência de código para navegação e análise. Ambos usam tree-sitter para parsing e grafos com Cypher. Mas a distância em performance e assertividade vem de escolhas arquiteturais fundamentais — não de detalhes de implementação.

Este relatório mapeia cada gap técnico com proposta concreta de melhoria.

---

## 1. Representação do Grafo

### fedora-nexus (atual)
- **Engine**: NetworkX `nx.DiGraph` in-memory
- **Node types**: `file`, `function`, `class`, `method`, `module`, `hook` — 6 tipos
- **Edge types**: `DEPENDS_ON`, `CONTAINS`, `CALLS` — 3 tipos
- **Indexação**: nenhuma; acesso por iteração linear

### GitNexus
- **Engine**: Grafo próprio com multi-index + LadybugDB para persistência
- **Node types**: 20+ tipos (`File`, `Folder`, `Function`, `Class`, `Interface`, `Method`, `Struct`, `Enum`, `Trait`, `Impl`, `Module`, `Route`, `Tool`, `Community`, `Process`, etc.)
- **Edge types**: 15+ tipos (`CALLS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `HAS_METHOD`, `ACCESSES`, `STEP_IN_PROCESS`, `MEMBER_OF`, etc.) com campo `confidence` por aresta
- **Multi-index in-memory**:
  - `Map<nodeId, Node>` — lookup por id O(1)
  - `Map<RelType, Map<relId, Rel>>` — filtro por tipo sem full-scan
  - `Map<nodeId, Set<relId>>` — remoção de arestas sem iteração global
  - `Map<filePath, Set<nodeId>>` — invalidação por arquivo O(file-nodes)

### Gap Crítico
O fedora-nexus usa NetworkX que exige iteração O(n) para a maioria das operações de filtro. Sem índices por tipo de aresta ou por arquivo, qualquer consulta passa pelo grafo inteiro. GitNexus resolve isso com multi-index mantido consistentemente em cada mutação.

---

## 2. Pipeline de Indexação

### fedora-nexus (atual)
- **Passes**: 3 passes sequenciais (file collection → symbol extraction → CALLS edges)
- **Concorrência**: single-threaded; apenas `asyncio.to_thread()` para não bloquear o servidor MCP
- **Linguagens**: 4 (Python, TypeScript, JavaScript, Ruby)
- **CALLS edges**: apenas Python, apenas funções top-level
- **Extração de imports**: básica; resolve path mas não segue re-exports
- **Incremental**: `force_reindex=True` apaga tudo e re-indexa do zero

### GitNexus
- **Phases**: 12 fases em DAG topológico (scan → structure → parse → routes/tools/orm → crossFile → scopeResolution → mro → communities → processes)
- **Concorrência**: Worker pool com `os.cpus().length` workers; sub-batches de 1500 arquivos ou 8MB; backpressure via structured-clone cost tracking; retry com binary split em timeout
- **Linguagens**: 13 (JS, TS, Python, Java, C#, C++, Go, Rust, Ruby, PHP, Swift, Dart, Kotlin)
- **CALLS edges**: resolução multi-etapa com MRO walk, receiver inference, confidence tiers
- **Cross-file type propagation**: fechamento topológico sobre DAG de imports
- **Incremental**: `removeNodesByFile(path)` → re-parse apenas arquivo modificado

### Gap Crítico
O maior gargalo do fedora-nexus em repositórios grandes é o single-threading. GitNexus paraleliza no nível do worker OS, não apenas coroutines asyncio. A falta de resolução de CALLS para TypeScript/Ruby deixa ~60% das arestas semânticas faltando nos repos mais comuns.

---

## 3. Busca e Navegação

### fedora-nexus (atual)
- **Mecanismo**: BM25 via Kuzu FTS indexes (keyword only)
- **Cypher**: subset próprio parseado com Lark; execução via NetworkX
- **Blast radius**: BFS simples sobre arestas reversas
- **Ranking**: ordenação por score FTS; sem fusão de múltiplas fontes

### GitNexus
- **Mecanismo**: Hybrid BM25 + Semantic via transformers.js (snowflake-arctic-embed-xs, 384 dims, 22M params)
- **Fusion**: Reciprocal Rank Fusion (RRF com k=60) que combina ranking keyword + semantic sem normalização de score
- **Cypher**: nativo no LadybugDB; queries reais passam diretamente para o engine
- **Impact tool**: blast radius multi-direcional com risk scoring (upstream + downstream + cross-boundary via Contract Bridge)
- **Context tool**: visão 360° de um símbolo (callers, callees, accesses, processos)
- **Community detection**: algoritmo de Leiden (graphology); detecta clusters funcionais; resultado exposto via `MEMBER_OF` edges

### Gap Crítico
Busca só por keyword retorna resultados por correspondência textual — não por relevância semântica. Um dev buscando "user authentication flow" no fedora-nexus retorna arquivos com essas palavras; no GitNexus retorna os símbolos semanticamente mais próximos, mesmo que nomeados `AuthController.validate()`. A diferença de "assertividade" que o usuário percebe vem quase inteiramente daqui.

---

## 4. Tecnologias

| Componente | fedora-nexus | GitNexus |
|-----------|----------|----------|
| Linguagem principal | Python | TypeScript |
| Grafo in-memory | NetworkX nx.DiGraph | Custom multi-index Map |
| Banco de dados | Kuzu (embedded) | LadybugDB (embedded) |
| Cypher engine | Lark (subset custom) | Nativo LadybugDB |
| Parser | tree-sitter (4 langs) | tree-sitter (13 langs) |
| Busca keyword | Kuzu FTS (BM25) | LadybugDB FTS (BM25) |
| Busca semântica | — | transformers.js + ONNX |
| Embedding model | — | snowflake-arctic-embed-xs (22M params) |
| Concorrência | asyncio coroutines | Worker threads OS-level |
| Community detection | — | Leiden (graphology) |
| MRO resolution | — | c3, ruby-mixin, first-wins |
| Incremental index | Full re-index | Por arquivo (removeNodesByFile) |
| Protocolo | MCP + HTTP | MCP + HTTP |

---

## 5. Gaps por Categoria de Impacto

### 🔴 Impacto Alto (afeta diretamente assertividade percebida)

#### G1 — Busca Semântica ausente
O fedora-nexus não tem embeddings. A busca retorna apenas correspondências de substring em nome e conteúdo.  
**Proposta**: integrar `sentence-transformers` com modelo leve (all-MiniLM-L6-v2 ou nomic-embed-text-v1.5) para gerar embeddings de símbolos. Armazenar no Kuzu com HNSW via `pg_vector` ou em arquivo FAISS/HNSWlib local. Aplicar RRF para fusão com BM25.

#### G2 — CALLS edges ausentes para TypeScript/JavaScript/Ruby
Sem arestas de chamada para as linguagens mais comuns, blast radius e context não conseguem propagar dependências reais.  
**Proposta**: implementar call extraction para TypeScript/JavaScript via tree-sitter seguindo o mesmo padrão Python. Para TypeScript, usar `call_expression` + `new_expression` nodes no AST. Para Ruby, usar `call` node.

#### G3 — Sem resolução de tipos cross-file
O fedora-nexus resolve imports mas não propaga tipos entre arquivos. Um método `UserService.save()` chamado em `orders.ts` não é conectado à definição real em `user_service.ts`.  
**Proposta**: implementar uma fase de "binding accumulator" após o parse: para cada call node não-resolvido, buscar no grafo de imports o símbolo mais próximo por nome.

### 🟡 Impacto Médio (afeta performance e cobertura)

#### G4 — Single-threading no indexador
Em repositórios com >500 arquivos, a indexação fica visivelmente lenta pois todo o trabalho é sequencial.  
**Proposta**: usar `concurrent.futures.ProcessPoolExecutor` para paralelizar o parse de arquivos. Cada worker recebe um batch de arquivos, retorna lista de nodes+edges, o processo principal merge no grafo. Python GIL não afeta isso pois tree-sitter é C extension.

#### G5 — Incremental indexing full re-index
Qualquer mudança força re-index completo. Em repositórios médios (1k+ arquivos), isso leva vários segundos.  
**Proposta**: rastrear `mtime` ou hash SHA de cada arquivo indexado. No `index_repo`, comparar estado atual com registry; re-parsear apenas arquivos com diff. Adicionar `remove_file(path)` que deleta nodes/edges do arquivo no Kuzu antes de re-inserir.

#### G6 — Cobertura de linguagens limitada (4 vs 13)
Java, Go, C#, Rust, PHP, Swift são comuns em monorepos enterprise.  
**Proposta**: adicionar parsers tree-sitter para Go e Java prioritariamente (maior demanda). O padrão `BaseIndexer` já existe; apenas implementar `_extract_symbols` e `_extract_imports` para cada nova linguagem.

#### G7 — Cypher via Lark (subset) vs Cypher nativo
O parser Lark limita quais queries são suportadas. Queries complexas (múltiplos MATCH, WITH, UNWIND) não funcionam.  
**Proposta**: Kuzu já tem Cypher nativo. O problema atual é que o grafo é persistido em Kuzu mas as queries são executadas via NetworkX (load → NetworkX → query). Migrar `query_graph` para executar Cypher diretamente no Kuzu sem intermediário NetworkX.

### 🟢 Impacto Baixo (melhorias de qualidade)

#### G8 — Sem community detection
Não existe detecção de clusters funcionais (ex: "esse módulo pertence ao domínio de pagamentos").  
**Proposta**: após indexação, rodar algoritmo de Louvain/Leiden via `networkx` ou `cdlib` sobre o grafo de `CALLS`+`IMPORTS`. Criar nodes `Community` com `MEMBER_OF` edges. Expor via `get_communities` tool.

#### G9 — Sem confidence score nas arestas
Todas as arestas têm o mesmo peso. Não dá pra distinguir import direto de chamada inferida.  
**Proposta**: adicionar campo `confidence` às edges (0.0–1.0). Imports diretos = 1.0, CALLS resolvido = 0.9, CALLS inferido = 0.7.

#### G10 — Sem staleness detection
Não há aviso ao usuário quando o grafo está desatualizado em relação ao estado atual do repo.  
**Proposta**: registrar o commit SHA do HEAD na indexação. Em cada tool call, verificar se HEAD mudou via `git rev-parse HEAD`. Se sim, incluir hint na resposta.

---

## 6. Proposta de Roadmap

### Sprint 1 — Fundação de Performance (G4 + G5 + G7)
> Objetivo: indexação rápida em repos médios e queries nativas

1. **Parallelizar indexador**: `ProcessPoolExecutor` com batch por CPU count
2. **Incremental indexing**: mtime tracking + `remove_file()` no Kuzu
3. **Queries nativas Kuzu**: remover intermediário NetworkX do caminho de `query_graph`

### Sprint 2 — Assertividade Semântica (G1 + G2)
> Objetivo: fechar o gap de relevância nas buscas

4. **CALLS edges para TypeScript**: call extraction via tree-sitter
5. **Busca semântica**: embeddings com nomic-embed-text-v1.5 + HNSWlib + RRF fusion

### Sprint 3 — Cobertura e Profundidade (G3 + G6 + G10)
> Objetivo: cobertura de linguagens enterprise e detecção de staleness

6. **Cross-file binding**: binding accumulator para tipos não-resolvidos
7. **Go + Java parsers**: novos `BaseIndexer` implementations
8. **Staleness detection**: Git SHA tracking + hints nas tool responses

### Sprint 4 — Inteligência do Grafo (G8 + G9)
> Objetivo: contexto semântico avançado

9. **Community detection**: Louvain/Leiden com nodes `Community`
10. **Confidence scores**: confidence field nas edges

---

## 7. Prioridade por ROI

| # | Melhoria | Esforço | Impacto | ROI |
|---|----------|---------|---------|-----|
| 1 | Queries nativas Kuzu (G7) | Baixo | Alto | ⭐⭐⭐⭐⭐ |
| 2 | CALLS edges TypeScript (G2) | Médio | Alto | ⭐⭐⭐⭐⭐ |
| 3 | Incremental indexing (G5) | Médio | Alto | ⭐⭐⭐⭐ |
| 4 | Busca semântica + RRF (G1) | Alto | Alto | ⭐⭐⭐⭐ |
| 5 | Parallelizar indexador (G4) | Médio | Médio | ⭐⭐⭐⭐ |
| 6 | Cross-file binding (G3) | Alto | Alto | ⭐⭐⭐ |
| 7 | Go + Java parsers (G6) | Médio | Médio | ⭐⭐⭐ |
| 8 | Staleness detection (G10) | Baixo | Médio | ⭐⭐⭐ |
| 9 | Community detection (G8) | Alto | Baixo | ⭐⭐ |
| 10 | Confidence scores (G9) | Baixo | Baixo | ⭐⭐ |

---

## 8. Nota sobre Ética e Originalidade

Esta análise não propõe clonar GitNexus. As melhorias listadas são:
- Uso de tecnologias open-source independentes (sentence-transformers, HNSWlib, ProcessPoolExecutor, Leiden via networkx)
- Patterns arquiteturais não-proprietários (multi-index, RRF, incremental indexing por mtime)
- Extensões naturais do que o fedora-nexus já faz — mas feitas com mais profundidade

O fedora-nexus tem vantagem no stack Python (melhor interop com ferramentas de análise estática existentes, mais fácil integrar com outras libs do ecossistema de AI/ML) e na simplicidade do deploy (sem Node.js, sem build step). Isso deve ser preservado.

---

*Gerado em: 2026-05-07*
