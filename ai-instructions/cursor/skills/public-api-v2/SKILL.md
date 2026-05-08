---
name: public-api-v2
description: Defines the Public API V2 architecture and implementation standards for controllers, handlers, serializers, nested query processors, routes, multi-tenancy, render_from_error, pagination, and testing. Use when implementing or reviewing Public API V2 Admin API or End User API endpoints (including nested resources), handlers, serializers, or OpenAPI/rswag changes.
---

# Public API V2 — Implementation Instructions

## Companion skill

For implementation tasks, also apply `development-and-validation-loop` to enforce deterministic seed + curl validation + final report.

## Overview

The **Admin API V2** lives under the `PublicApi::AdminApi::V2` namespace, is served on the developer API host (see `TeachableDomain.developers_api` and `AGENTS.md`), and in routes uses the **`/kong_api/v2`** prefix to align with the gateway.

The pattern to follow in practice is: **thin controller** → **handler** (use case / orchestration + rules) → **query** (`Queries::*::QueryProcessor` when filters/pagination are non-trivial) → **serializer** → consistent JSON responses. Handler errors return **`Monads::Failure`**; the controller uses **`render_from_error(result.value)`** (don't spread `render_validation_error` manually when the handler already returns `Failure`).

**Recommended canonical reference** (complete real flow in the repository):

| Step | Where |
|--------|------|
| Nested routes + `module` | `config/routes.rb` (`namespace :v2` → `namespace :products` → `resources :product_collections` → `resources :products, module: :product_collections`) |
| Controller | `app/controllers/public_api/admin_api/v2/products/product_collections/products_controller.rb` |
| Handler | `app/services/public_api/v2/product_collections/product_handler.rb` |
| Query / filters / pagination | `app/services/public_api/v2/queries/product_collections/products/query_processor.rb` (+ `filters/` in the same directory when it exists) |
| Serializer | `app/serializers/public_api/v2/product_collection_product_serializer.rb` |
| Base controller (response helpers) | `app/controllers/public_api/admin_api/v2/application_controller.rb` |

Use this chain as a model when adding new V2 endpoints.

---

## 1. Routes (`config/routes.rb`)

### ### V2 Routes under `public_api` / `admin_api` ###

`config/routes.rb`  

**Purpose:** Declare endpoints under `namespace :public_api` with host constraint, then `namespace :admin_api, path: "kong_api"` and `namespace :v2`.

Patterns observed in V2:

- **HTTP prefix:** `/kong_api/v2/...` (not `/v2/...` loose in the Fedora app).
- **Kebab-case in path:** e.g.: `resources :product_collections, path: "product-collections"`.
- **Nested resources:** use `module: :module_name` so Rails resolves controllers in subfolders without prefixing the class name with the repeated parent module (e.g.: `module: :product_collections` → `…/product_collections/products_controller.rb`).
- **Special collection routes:** e.g. `delete ":type/:id", to: "products#destroy", on: :collection` when the standard REST verb doesn't cover the API contract.

Reference snippet (products inside product collection):

```ruby
resources :product_collections, except: [:new, :edit], path: "product-collections" do
  resources :products, only: [:index, :create], module: :product_collections do
    delete ":type/:id", to: "products#destroy", on: :collection
  end
end
```

---

## 2. Controller (`PublicApi::AdminApi::V2::…`)

### ### Products::ProductCollections::ProductsController ###

`app/controllers/public_api/admin_api/v2/products/product_collections/products_controller.rb`  

**Purpose:** Authorize context (via `ApplicationController` / Kong), read params, call **only** the handler, render success or delegate error to `render_from_error`.

Conventions:

1. **Serializer alias** at the top of the class (readability and smaller diff).
2. **Handler call** with explicit keywords (`school: current_school`, parent ids from `params`, `filters`/`page`/`limit` according to contract).
3. **Success — paginated list:** `render_paginated_data(result.value, serializer: YourSerializer)` (uses `is_collection: true` internally).
4. **Success — payload in `data` without pagination:** `render_data(...)` (accepts already serializable structure / serializers).
5. **Failure:** `render_from_error(result.value)` — the `value` of a `Failure` is the **Hash** with keys like `:reason`, `:message`, `:errors`.
6. **`resource_name`:** implement the private method `resource_name` when the controller uses `render_from_error` (generic not found messages use this).
7. **Destroy without body:** `render status: 204` on success, when applicable.

Example faithful to current pattern (index + failure):

```ruby
def index
  result = PublicApi::V2::ProductCollections::ProductHandler.get_all(
    school: current_school,
    product_collection_id: params[:product_collection_id],
    filters: { type: params[:type] },
    page: params[:page],
    limit: params[:limit],
  )

  if result.success?
    render_paginated_data result.value, serializer: ProductCollectionProductSerializer
  else
    render_from_error result.value
  end
end
```

**Note:** Some older controllers still do `render json: { … }` + `render_validation_error(values[:errors])` manually. For new code, **prefer** `render_paginated_data` / `render_data` + `render_from_error` to map errors uniformly.

---

## 3. Handler (`PublicApi::V2::…`)

### ### ProductCollections::ProductHandler ###

`app/services/public_api/v2/product_collections/product_handler.rb`  

**Purpose:** Centralize business rules, input validations, school scoping (and by parent resource), transactional orchestration and return **`Success`** / **`Failure`**.

Two common profiles:

### ### A) "Simple" handler (CRUD on one model) ###

Inherits `PublicApi::V2::BaseHandler`, implements `model`, `base_relation(school)` (and optionally `allowed_attributes` for create/update). Reuses `get_all`, `get_by_id`, `create`, `update`, `destroy` when appropriate.

Reference example: `PublicApi::V2::CoachingHandler` in `app/services/public_api/v2/coaching_handler.rb`.

### ### B) Composite handler / nested domain ###

- Lives in subdirectory: `app/services/public_api/v2/<domain>/…_handler.rb`.
- May **not** expose single `model` if the operation aggregates multiple tables (like `ProductCollections::ProductHandler`).
- Defines explicit class methods (`get_all`, `create`, `destroy`, …) with clear signature and scope **always** derived from `school` (and parent id, e.g. `product_collection_id`).
- Encapsulates complex queries in **`PublicApi::V2::Queries::…::QueryProcessor`** (subclass of `PublicApi::V2::Queries::QueryProcessor`), instead of building loose SQL/arel in the controller.

Returns:

- Success: `Success.new(payload)` — for paginated lists via `QueryProcessor`, the payload follows the format expected by `render_paginated_data` (e.g.: `items`, `current_page`, `per_page`, `total_pages`, `total_count`).
- Failure: `BaseHandler` helpers (`not_found_failure`, `validation_failure`, `invalid_request_failure`, `forbidden_failure`, …) or `Failure.new(reason:, message:, errors:)` coherent with `render_from_error`.

---

## 4. Queries (`PublicApi::V2::Queries::…`)

### ### ProductCollections::Products::QueryProcessor ###

`app/services/public_api/v2/queries/product_collections/products/query_processor.rb`  

**Purpose:** Apply filters, sorting and pagination over the relation built by the handler, keeping query rules **outside** the controller.

- Inherits `PublicApi::V2::Queries::QueryProcessor`.
- Overrides `filter_class` to point to a specific filters module/class (`…::Filters`) when needed.
- The handler calls `…::QueryProcessor.apply(relation:, filters:, sort:, page:, limit:)` (or equivalent signature documented in that processor).

Golden rule: **controllers don't build complex scopes**; this stays in the handler + query layer.

---

## 5. Serializers (`PublicApi::V2::…`)

### ### ProductCollectionProductSerializer ###

`app/serializers/public_api/v2/product_collection_product_serializer.rb`  

**Purpose:** Define the JSON contract for the resource (attributes, types, calculated fields).

- "Product" resources often inherit `PublicApi::V2::BaseProductSerializer` when it makes sense (`CourseSerializer`, etc.).
- Other resources use **`ActiveModel::Serializer`** directly, like `ProductCollectionProductSerializer`.

### ### ActiveModel::Serializer 0.8.x — what to declare and what **not** to repeat ###

`app/serializers/public_api/v2/school_customization_serializer.rb` (pattern "only override where the model lies")  

**Purpose:** Avoid verbose serializers duplicating `def` for each column when AMS already exposes the attribute correctly.

1. **Default rule:** list everything in **`attributes :id, :name, …`**. The macro registers the fields and, for each one, AMS generates a method that delegates to `object` via `read_attribute_for_serialization` — in ActiveRecord this equals **`object.send(:attribute_name)`** for normal columns. **Don't** add `def site_logo_url; object.read_attribute(:site_logo_url); end` (and similar) if the model **doesn't** redefine the reader; this is noise and tends to diverge from the rest of V2 convention.

2. **When to override on purpose:** if the **model** redefines the attribute reader (computed, theme default, aggregate, etc.) and the public API needs the **value persisted in the column** (or another explicit rule), declare the name in `attributes` and implement **only** those methods **after** the `attributes` block, using `object.read_attribute(:field)` (or `.presence` if the contract requires normalizing empty → `null`). Real example: `SchoolCustomization` redefines **color** readers from `CUSTOMIZATION_DEFAULTS`; the serializer only keeps a loop `HEX_ATTRIBUTES.each { define_method(...) { object.read_attribute(...).presence } }` and leaves the rest of attributes to AMS.

3. **Order matters:** in 0.8.x, `attributes :foo` can generate automatic `def foo`; a `def foo` written **below** in the same class body replaces the generated one — use this only for the subset that needs safe reading.

4. **Reference:** `PublicApi::V2::SchoolCustomizationSerializer` — complete `attributes` + minimal overrides for hex/checkout colors.

For paginated collections, the controller uses:

```ruby
serializer.new(item, is_collection: true)
```

(respect `is_collection` in serializers that differentiate list vs. detail.)

---

## 6. Errors and `render_from_error`

### ### ApplicationController (Admin API V2) ###

`app/controllers/public_api/admin_api/v2/application_controller.rb`  

**Purpose:** Map `result.value` from a `Failure` to HTTP status/body.

`render_from_error` currently handles, among others, `:reason` in:

- `:not_found` → `render_not_found_error`
- `:invalid_request` → `render_invalid_request_error`
- `:validation_failed`, `:validation_error` → `render_validation_error`
- `:forbidden` → `render_forbidden_error`
- `:tier_not_deletable` → `render_conflict_error`
- others → `render_internal_error`

Handlers should use **`reason:`** compatible with this table when returning `Failure`.

---

## 7. Pagination and collection format

- **`render_paginated_data`:** response with `data` + `meta` (`default_pagination_meta`).
- Page parameters: the reference product uses `page` and **`limit`** in the handler; other endpoints may use `per_page` — align to OpenAPI contract and to that resource's `QueryProcessor`.
- Collections **without** pagination (e.g.: empty list or 0/1 items per product contract) can respond only with `data` without `meta` — document in OpenAPI and maintain consistency with similar endpoints.

---

## 8. Multi-tenant and security

- Every operation must be anchored in **`current_school`** (from Kong / `PublicApi::ApplicationController`).
- Handlers must receive `school:` and restrict queries by **`school_id`** or association (`school.courses`, etc.). **Never** global scope without `school`.
- When there's a parent resource (e.g.: product collection), validate existence **and ownership by school** before listing children (as in `ProductHandler.get_all` with `ProductCollection.exists?(id:, school:)`).

---

## 9. Tests

- **Request specs** are the norm for Admin API V2: `spec/requests/public_api/admin_api/v2/...`.
- Use **`KongHelper`** + `set_kong_host` / `host! TeachableDomain.developers_api.host` according to existing examples.
- Validate OpenAPI contract with `assert_request_schema_confirm` / `assert_response_schema_confirm` when the path is documented in `open_api/public_api/admin_api/v2/api.yaml`.
- **Handler specs:** `spec/services/public_api/v2/...` (including subfolders like `product_collections/` when it exists).
- **Serializer specs:** `spec/serializers/public_api/v2/...` when there's non-trivial logic.

---

## 10. OpenAPI

New public endpoints should be described in **`open_api/public_api/admin_api/v2/api.yaml`** (paths with `/kong_api/v2/...`), aligned to actual behavior and Committee tests.

---

## 11. Checklist when adding a V2 endpoint

1. Route under `kong_api/v2`, with correct path and `module:` for the controller file.
2. Controller: serializer constant, handler call, `render_paginated_data` / `render_data` / HTTP status, `render_from_error`, `resource_name` if needed.
3. Handler: scope by `school` (and parent); returns `Success`/`Failure` with compatible `reason`.
4. If the query is complex: `Queries::<Domain>::…::QueryProcessor` + dedicated filters.
5. Serializer: prefer only `attributes` + specific overrides (`read_attribute`) when the model customizes the reader; tests (request; handler; serializer if applicable).
6. OpenAPI updated.

---

## 12. Quick references in the repository

| Piece | Path |
|------|---------|
| Base handler | `app/services/public_api/v2/base_handler.rb` |
| Query processor base | `app/services/public_api/v2/queries/query_processor.rb` |
| Controller base V2 | `app/controllers/public_api/admin_api/v2/application_controller.rb` |
| Canonical flow (nested) | `app/controllers/public_api/admin_api/v2/products/product_collections/products_controller.rb` |
| Canonical handler (nested) | `app/services/public_api/v2/product_collections/product_handler.rb` |
| Canonical query processor | `app/services/public_api/v2/queries/product_collections/products/query_processor.rb` |
| Canonical serializer | `app/serializers/public_api/v2/product_collection_product_serializer.rb` |
| Serializer AMS 0.8 (attributes + minimal overrides) | `app/serializers/public_api/v2/school_customization_serializer.rb` |
| V2 product routes | `config/routes.rb` (search for `namespace :v2` and `product-collections`) |

---

## 13. Nested resources (folder structure)

Maintain **parity** between URL, `module:` in routes and Ruby namespaces:

```
app/controllers/public_api/admin_api/v2/products/product_collections/
└── products_controller.rb   # PublicApi::AdminApi::V2::Products::ProductCollections::ProductsController

app/services/public_api/v2/product_collections/
└── product_handler.rb         # PublicApi::V2::ProductCollections::ProductHandler

app/services/public_api/v2/queries/product_collections/products/
└── query_processor.rb
```

For other nestings (courses → lessons → …), replicate the same idea: folder reflects hierarchy; handler/query may gain sub-namespaces aligned to the domain.
