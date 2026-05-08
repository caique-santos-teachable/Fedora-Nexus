---
name: public-api-v2-admin-routes
description: Use this skill when implementing or reviewing Public API V2 Admin endpoints. Covers routes, namespaces, controllers, handlers, serializers, query filters, sorting, response helpers, OpenAPI and rswag.
---

# Public API V2 Admin Routes Skill

Use this skill to implement new Admin API V2 endpoints with the same conventions used across products, users, transactions, uploads, and customizations.

## 1) Scope and canonical references

Primary references:

- `config/routes.rb`
- `app/controllers/public_api/admin_api/v2/application_controller.rb`
- `app/controllers/concerns/public_api/v2/shared_response_helpers.rb`
- `open_api/public_api/admin_api/v2/api.yaml`
- `open_api/rswag/admin_api/v2/**/*.rb`

## 2) Route design rules (mandatory)

Define endpoints under:

- `namespace :public_api, path: nil, constraints: { host: TeachableDomain.developers_api.host }`
- `namespace :admin_api, path: "kong_api"`
- `namespace :v2`

Conventions:

- Prefix is `/kong_api/v2/...` in runtime.
- Use kebab-case paths where contract requires (e.g. `pricing-plans`, `product-collections`, `quiz-responses`).
- For nested resources, prefer `scope module: :<parent>` or `module: :<parent>` to keep namespace/file-path parity.

Reference file: `config/routes.rb`

## 3) Namespace and folder parity

Always keep URL, route module, file path and Ruby namespace aligned.

Example:

- route: `resources :product_collections, module: :product_collections`
- file: `app/controllers/public_api/admin_api/v2/products/product_collections/products_controller.rb`
- namespace: `PublicApi::AdminApi::V2::Products::ProductCollections`

## 4) Controller rules (thin controller, hard requirement)

Controller responsibilities only:

1. Parse/permit params
2. Call handler
3. Render unified success/error response

Conventions:

- Inherit from `PublicApi::AdminApi::V2::ApplicationController`
- Define serializer and handler constants
- Use `resource_name` for shared error messages
- Use:
  - `render_paginated_data(...)` for lists
  - `render_data(...)` for single payloads
  - `render status: 204` for successful delete without body
  - `render_from_error(result.value)` for failures

## 5) ApplicationController contract

Admin v2 base controller enforces:

- v2 feature-flag gate (`validate_api_version`)
- school plan gate (`check_school_permission`)
- shared error rendering via `PublicApi::V2::SharedResponseHelpers`
- exception handling with `handle_bad_request` / `handle_exception`

Reference file: `app/controllers/public_api/admin_api/v2/application_controller.rb`

## 6) Handler rules (business/data access)

Handlers should:

- Inherit from `PublicApi::V2::BaseHandler` when possible
- Return `Success` / `Failure` (Monads contract)
- Validate school ownership and parent-resource ownership
- Keep query logic in handlers/query classes (not in controllers)
- Reuse `get_all_paginated(...)` and BaseHandler helpers for consistency

Failure payloads must stay compatible with `render_from_error`:

- `reason:` (`:not_found`, `:invalid_request`, `:validation_failed`, `:validation_error`, `:forbidden`, `:conflict`)
- optional `message:`, `errors:`, `error_code:`

## 7) Query filters and sorting rules

For list endpoints with non-trivial filters/sorting:

- Use dedicated classes under `app/services/public_api/v2/queries/...`
- Filters inherit `PublicApi::V2::Queries::BaseFilters`
- Sorting inherits `PublicApi::V2::Queries::Sorting`
- Keep `ALLOWED_FIELDS` explicit
- Use deterministic filters (invalid inputs should not explode query execution)

## 8) Serializer rules

Serializer contract must mirror OpenAPI schema exactly.

Conventions:

- `ActiveModel::Serializer`
- `attribute` declarations first
- computed/nested fields via methods
- boolean normalization where needed (`!!value`)
- stable href patterns for resource links (`/api/v2/...`)

## 9) Response contract rules

Use shared response helpers:

- list success: `{ data: [...], meta: {...} }`
- item success: `{ data: {...} }`
- errors: `{ error: { code, message, details } }`

Avoid custom ad-hoc response envelopes in controllers.

## 10) Parameter handling rules

- Use explicit `params.permit(...).to_h.symbolize_keys`
- Keep pagination mapping consistent (`page`, `per_page` -> handler `limit`)
- Keep path ids explicit in handler calls (`user_id`, `product_collection_id`, `tier_id`, etc.)

## 11) OpenAPI and rswag rules

For every new/changed endpoint:

1. Update `open_api/public_api/admin_api/v2/api.yaml`
2. Update corresponding rswag file in `open_api/rswag/admin_api/v2/...`
3. Keep schemas/statuses/required fields fully aligned with runtime behavior

Minimum rswag coverage:

- success response
- forbidden/feature-gate/permission path when applicable
- not found for member routes
- validation/invalid request path when filters/pagination have validation

## 12) depgraph CLI protocol (safe changes)

Before substantial edits:

1. `depgraph blast-radius $REPO_PATH <changed_files...> --json`
2. `depgraph deps $REPO_PATH <file> --depth 2 --json`
3. `depgraph dependents $REPO_PATH <file> --json`

## 13) Implementation template (copy and adapt)

Controller skeleton:

Reference file: `app/controllers/public_api/admin_api/v2/products/digital_downloads_controller.rb`

```ruby
module PublicApi::AdminApi::V2::<Namespace>
  class <ResourcesController> < PublicApi::AdminApi::V2::ApplicationController
    Handler = PublicApi::V2::<Handler>
    Serializer = PublicApi::V2::<Serializer>

    def index
      result = Handler.get_all(
        school: current_school,
        filters: filter_params,
        sort_by: params[:sort_by].presence,
        sort_direction: params[:sort_direction].presence,
        page: params[:page],
        limit: params[:per_page]
      )

      data = result.value
      if result.success?
        render_paginated_data(data, serializer: Serializer)
      else
        render_from_error(data)
      end
    end

    def show
      result = Handler.get_by_id(id: params[:id], school: current_school)
      data = result.value
      if result.success?
        render_data(Serializer.new(data))
      else
        render_from_error(data)
      end
    end

    private

    def resource_name
      "<Resource>"
    end

    def filter_params
      params.permit(:field_a, :field_b).to_h.symbolize_keys
    end
  end
end
```

Handler skeleton:

Reference file: `app/services/public_api/v2/users/purchase_handler.rb`

```ruby
module PublicApi::V2::<Namespace>
  class <Handler> < PublicApi::V2::BaseHandler
    class << self
      def get_all(school:, filters: {}, page: nil, limit: nil, **context)
        relation = base_relation(school, **context)

        get_all_paginated(
          relation: relation,
          filters: filters,
          filter_class: PublicApi::V2::Queries::<Namespace>::Filters,
          sorting_class: PublicApi::V2::Queries::<Namespace>::Sorting,
          page: page,
          limit: limit
        )
      end

      def get_by_id(id:, school:, **context)
        record = base_relation(school, **context).find_by(id: id)
        return not_found_failure("<Resource> not found") unless record

        Success.new(record)
      end
    end
  end
end
```

Serializer skeleton:

Reference file: `app/serializers/public_api/v2/user_purchase_serializer.rb`

```ruby
module PublicApi::V2
  class <ResourceSerializer> < ActiveModel::Serializer
    attribute :id
    attribute :created_at
    attribute :updated_at
    attribute :related_resource
    attribute :is_active

    def related_resource
      return nil if object.<association>.blank?

      {
        id: object.<association>.id,
        href: "/api/v2/<resource_path>/#{object.<association>.id}"
      }
    end

    def is_active
      !!object.is_active
    end
  end
end
```

Query filter skeleton:

Reference file: `app/services/public_api/v2/queries/users/purchases/filters.rb`

```ruby
module PublicApi::V2::Queries::<Namespace>
  class Filters < PublicApi::V2::Queries::BaseFilters
    def self.filters
      {
        created_after: ->(relation, value) {
          begin
            time = Time.iso8601(value.to_s)
            relation.where("created_at > ?", time)
          rescue ArgumentError
            relation
          end
        },
        status: ->(relation, value) { relation.where(status: value) },
        is_active: ->(relation, value) {
          bool = normalize_boolean(value)
          bool.nil? ? relation : relation.where(is_active: bool)
        }
      }
    end
  end
end
```

Sorting skeleton:

Reference file: `app/services/public_api/v2/queries/users/purchases/sorting.rb`

```ruby
module PublicApi::V2::Queries::<Namespace>
  class Sorting < PublicApi::V2::Queries::Sorting
    ALLOWED_FIELDS = %i[created_at updated_at id].freeze
    DEFAULT_FIELD = :created_at
    DEFAULT_DIRECTION = :desc
  end
end
```

## 14) Anti-patterns (avoid)

- business logic in controllers
- missing school scoping in handlers
- bypassing shared response helpers
- inconsistent pagination param names
- serializer/OpenAPI contract drift
- adding filters/sorting in controller instead of query classes
