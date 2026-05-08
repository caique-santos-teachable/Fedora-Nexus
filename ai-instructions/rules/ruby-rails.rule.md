---
description: Ruby, Rails e Rubocop — guardrails de qualidade para projetos Ruby on Rails. Padrões de código, ActiveRecord, callbacks, Rubocop e convenções de teste.
applyTo: "**/*.rb"
---

# Ruby / Rails — Quality Guardrails

## Checklist obrigatório antes de concluir uma mudança Ruby/Rails

- Simplificar lógica redundante e evitar construções desnecessárias.
- Evitar padrões verbosos quando uma forma direta e legível resolve o problema.
- Revisar risco de N+1 em handlers, serializers e queries.
- Validar contratos de serializer para não retornar atributo incorreto por colisão de nome.
- Executar testes relevantes e confirmar comportamento esperado.
- Executar `rubocop <arquivos_modificados>` antes de considerar a tarefa concluída.

## Padrões de prevenção de erro

1) Redundância e legibilidade
- Evitar expressões redundantes como `presence.present?`.
- Preferir `present?`, `blank?` ou checagem direta, conforme o caso.
- Evitar helpers supérfluos para lógica trivial (ex.: one-liner claro).

2) N+1 e performance de consulta
- Sempre avaliar acesso a associações em serializers e loops.
- Adicionar `includes`/`preload`/`joins` quando necessário para evitar N+1.
- Evitar carregar coleções inteiras para validar um item único (preferir `exists?` e subquery).

3) Serializer e acesso a atributos
- Quando houver risco de colisão de método (ex.: `name`), explicitar leitura segura do atributo.
- Em serialização de enums/campos calculados, centralizar regra no model quando fizer sentido.
- Confirmar que campos serializados existem para todos os tipos de entidade suportados.

4) Validações e filtros
- Garantir validações de parâmetros acopladas ao contexto de negócio.
- Evitar dupla validação desconexa; manter fluxo claro e previsível.
- Priorizar mensagens de erro consistentes e acionáveis.

## Regra de evolução contínua

5) Rubocop deve ser executado em arquivos modificados antes de concluir
- **Anti-pattern**: confiar apenas no hook de pre-commit para capturar `EmptyLinesAroundClassBody`, `MultilineMethodCallIndentation` e similares.
- **Padrão recomendado**: após finalizar a implementação, executar `rubocop <arquivos_modificados>` e corrigir antes de considerar a tarefa concluída.

6) `before_create` com `||=` — callback override invisível em dados de teste
- **Anti-pattern**: fabricar um modelo esperando trabalhar com queries filtradas por campo com `||=` no callback — o callback executa antes da inserção, sobrescrevendo o valor nil, tornando registros invisíveis aos filtros que esperavam nil.
- **Padrão recomendado**: usar fabricator nomeado que restaure o valor pretendido após a criação (via `after_create { update_column(...) }`), ou chamar `update_column` explicitamente no spec.
- **Diagnóstico**: se `base_relation` retorna 0 registros em teste mas os registros existem no banco, suspeitar de callback `||=`.
- **Verificação**:
  ```bash
  grep -n 'before_create\|before_save\|set_defaults' app/models/<model>.rb
  # Qualquer ||= em campo de filtro é candidato a problema
  ```

7) PostgreSQL `where.not(field: val)` exclui NULLs — dados de teste precisam de valor não-nulo explícito
- **Anti-pattern**: fabricar com `field: nil` e testar um scope que usa `where.not(field: val)` esperando que o registro seja incluído — em PostgreSQL, `col != val` é `NULL`-exclusivo.
- **Padrão recomendado**: ao testar qualquer scope com `where.not(col: val)`, garantir que os registros de teste têm valor **não-nulo** para aquela coluna.
- **Regra geral**: `where.not(col: val)` gera `col != val` em SQL — PostgreSQL exclui silenciosamente linhas com `NULL`.
- **Exemplo**:
  ```ruby
  # ❌ field é nil pelo fabricator — não retornado pelo scope
  let!(:record) { Fabricate(:my_model, school: school) }

  # ✅ field explícito e não-nil
  let!(:record) { Fabricate(:my_model, school: school, net_charge_usd: 100, status: "paid") }
  ```
