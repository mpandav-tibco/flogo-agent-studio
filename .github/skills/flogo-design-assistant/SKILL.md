# Flogo Design Assistant (FDA) Skill

This skill documents all tools, functions, and workflows for the TIBCO Flogo Design Assistant (FDA) MCP server v0.9.2. Use this whenever creating, modifying, or validating Flogo applications via FDA.

---

## 1. FDA Startup

The FDA MCP server must be started before using any tools:

```bash
EXT=/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442
APPS=/Users/milindpandav/git/flogo-agent-studio/apps

cd "$APPS"
"$EXT/bin/flogodesign-cli" mcp http 3333 --multiFileMode true
```

- All tool calls are relative to the `--multiFileMode` working directory (`$APPS`)
- `flogoFile` parameter = filename only (e.g. `agent-builder-service.flogo`), not full path
- FDA is also available as MCP tools via prefix `mcp_flogo-assista_*`

---

## 2. CRITICAL WORKFLOW RULES

> These must be followed every time — failure causes silent correctness bugs.

### Rule 1: After ANY mapping change → run `check-mappings`
```
check-mappings flogoFile=<file> flow=<flow>
```
Validates: $activity refs are upstream, $property refs exist, function imports present, @foreach scopes valid, actreturn keys match flow.metadata.output, HTTP statusCode non-zero.

### Rule 2: After ANY trigger input/output schema change → run `wire-trigger-handler`
```
wire-trigger-handler flogoFile=<file> trigger=<trigger> handler=<handler>
```
This scaffolds 4-step wiring automatically:
1. `flow.metadata.input[]` — copies trigger handler inputs to flow inputs
2. `handler.action.input` — maps flow inputs from trigger handler inputs
3. `flow.metadata.output[]` — copies reply fields to flow outputs
4. `handler.action.output` + `handler.reply` — maps flow outputs back to trigger reply

Use `--force` to re-wire even if already wired. Use `--inputs-only` or `--reply-only` for partial wiring.

### Rule 3: `make-mapping` for mapper values, `set-attribute` for everything else
- **`make-mapping`**: Sets values inside mapper `input.mapping.*` fields — supports Flogo expressions, literals, @foreach loops
- **`set-attribute`**: Sets any non-mapper config attribute (trigger settings, activity config fields, property values, connection settings)

### Rule 4: After set-attribute on JSON — check for double-serialization
If `set-attribute` is used on a JSON value (e.g. mapper input), verify the resulting JSON is not double-encoded (string containing escaped JSON). Always use `--jsonValue` flag for JSON objects and `--jsonFile` for files.

### Rule 5: Conditional links cannot be created via FDA
FDA `create-link` only creates unconditional success links. For conditional links (type=expression), edit the `.flogo` JSON directly, then validate with `check-mappings`.

### Rule 6: Always `check` after adding activities/triggers
Run `check flogoFile=<file> type=activity flow=<flow> name=<activity>` to confirm the item was created with correct attributes before proceeding with mappings.

---

## 3. ALL FDA TOOLS BY CATEGORY

### 3.1 Project & Flow Structure

#### `create-project` 
Create a new Flogo application file.
```
create-project name=<appName>
```

#### `create-flow` (alias: `cf`)
Add a new flow to an existing Flogo app. Auto-seeds a noop start activity.
```
create-flow flogoFile=<file> name=<flowName>
```

#### `describe-project`
Full project summary: flows, activities, triggers, connections, properties, schemas.
```
describe-project flogoFile=<file>
```

#### `list-flogo-projects`
List all `.flogo` files in the working directory.
```
list-flogo-projects
```

#### `create-api-skeleton`
Scaffold a complete REST API Flogo app from an OpenAPI spec.
```
create-api-skeleton spec=<specName>
```

#### `create-mcp-skeleton`
Scaffold a complete MCP server Flogo app.
```
create-mcp-skeleton name=<appName>
```

---

### 3.2 Activities

#### `create-activity` (alias: `ca`, `aa`)
Add an activity to a flow. Automatically links to the previous activity unless `--doNotLink` is set.
```
create-activity flogoFile=<file> flow=<flow> type=<activityRef> name=<name>
create-activity flogoFile=<file> flow=<flow> type=<activityRef> name=<name> --doNotLink
create-activity flogoFile=<file> flow=<flow> type=<activityRef> name=<name> --connection <connName>
```

**Key activity refs** (from flogo-agent-studio context):
| Ref | Short name | Purpose |
|-----|-----------|---------|
| `#act_rest_invoke` | rest | HTTP REST client |
| `#act_rest_reply` | rest-reply | Send HTTP response |
| `#act_general_mapper` | mapper | Map/transform data |
| `#act_general_log` | log | Write log message |
| `#act_general_jseval` | jseval | Execute JavaScript |
| `#act_general_actreturn` | actreturn | Return from flow with outputs |
| `#act_general_throw` | throw | Throw an error |
| `#act_general_setctxvalue` | setctxvalue | Set flow context value |
| `#act_general_getctxvalue` | getctxvalue | Get flow context value |
| `#act_general_iterator` | iterator | Iterate over array |
| `#act_general_noop` | noop | No-operation placeholder |

Use `list-activity-types flogoFile=<file>` to discover all available refs.

#### `change-activity-type`
Change the activity type (ref) of an existing activity, preserving its name and links.
```
change-activity-type flogoFile=<file> flow=<flow> activity=<name> type=<newRef>
```

#### `format-flow`
Auto-format all activity positions in a flow for clean visual layout.
```
format-flow flogoFile=<file> flow=<flow>
```

---

### 3.3 Links

#### `create-link` (alias: `cl`)
Create an unconditional success link between two activities.
```
create-link flogoFile=<file> flow=<flow> from=<activity1> to=<activity2>
```
> **Limitation**: Only creates unconditional links. For `type=expression` conditional links, edit the `.flogo` JSON directly.

#### `remove-link`
Remove a link between two activities. Uses internal link IDs, not just activity names.
```
remove-link flogoFile=<file> flow=<flow> from=<activity1> to=<activity2>
```
> **Limitation**: May fail when internal IDs don't match names. Workaround: direct JSON editing.

---

### 3.4 Triggers

#### `create-trigger`
Add a trigger to the Flogo app.
```
create-trigger flogoFile=<file> type=<triggerRef> name=<name>
```
Use `list-trigger-types flogoFile=<file>` to see available trigger refs.

Common trigger refs:
| Ref | Purpose |
|-----|---------|
| `#trg_rest_receiver` | HTTP REST server |
| `#trg_timer_timer` | Timer/cron trigger |
| `#trg_kafka_subscriber` | Kafka consumer |

#### `create-trigger-handler`
Create a handler on an existing trigger and link it to a flow.
```
create-trigger-handler flogoFile=<file> trigger=<name> flow=<flow>
```
After creating, always run `wire-trigger-handler` to scaffold the input/output wiring.

#### `wire-trigger-handler` (alias: `wth`) — CRITICAL
Wire trigger handler inputs/outputs to the linked flow's metadata.
```
wire-trigger-handler flogoFile=<file> trigger=<trigger> handler=<handler>
wire-trigger-handler flogoFile=<file> trigger=<trigger> handler=<handler> --force
wire-trigger-handler flogoFile=<file> trigger=<trigger> handler=<handler> --inputs-only
wire-trigger-handler flogoFile=<file> trigger=<trigger> handler=<handler> --reply-only
```
Default: skips fields already wired. Use `--force` to re-wire everything.

---

### 3.5 Connections

#### `create-connection`
Create a named connection of a specific type.
```
create-connection flogoFile=<file> type=<connRef> name=<name>
```
Use `list-connection-types flogoFile=<file>` to discover available connection types.

---

### 3.6 App Properties & Schemas

#### `create-app-property`
Create an application-level property. Selector format: `GROUP.NAME`.
```
create-app-property flogoFile=<file> name=<GROUP.NAME> type=<type> value=<value>
```
Types: `string`, `integer`, `boolean`, `object`, `array`

Reference in expressions: `=$property["GROUP.NAME"]` or `=$property["NAME"]`

#### `create-schema` (alias: `cs`)
Create a named JSON schema for use in set-mapping-schema.
```
create-schema flogoFile=<file> name=<schemaName> schema=<jsonSchema>
```

#### `create-spec`
Create an OpenAPI specification in the project.
```
create-spec flogoFile=<file> name=<specName>
```

---

### 3.7 Imports & Contributions

#### `add-import`
Add a Go package import to the project.
```
add-import flogoFile=<file> package=<goImportPath>
```

#### `remove-import`
Remove a Go package import.
```
remove-import flogoFile=<file> package=<goImportPath>
```

#### `add-contribution` / `remove-contribution`
Manage Flogo contributions (extensions) registered in the project.

#### `describe-imports`
List all imports currently in the project.
```
describe-imports flogoFile=<file>
```

---

### 3.8 Mapping Tools

#### `make-mapping` (alias: `mm`) — PRIMARY MAPPING TOOL
Set mapper field values using Flogo expressions. **Use this for all mapper input/output fields.**

Selector format: `<flow>.<activity>.input.input.mapping.<fieldPath>`

```
# Simple field mapping
make-mapping flogoFile=<file> selector=<flow>.<activity>.input.input.mapping.<field> value=<expr>

# With type coercion
make-mapping flogoFile=<file> selector=... value=<expr> --type string

# For JSON literal values
make-mapping flogoFile=<file> selector=... value=<json> --jsonValue

# With @foreach loop
make-mapping flogoFile=<file> selector=<flow>.<activity>.input.input.mapping.<arr> \
  value=<expr> --foreach "$flow.items" --as "item"
```

**Common expression patterns:**
```
# Reference activity output
=$activity[ActivityName].output.field
=$activity[ActivityName].responseBody.field   # REST response body

# Reference flow input
=$flow.body.fieldName
=$flow.pathParams.id

# Reference app property
=$property["GROUP.NAME"]

# String literal
=string literal value (starts with = for expression, no = for literal)

# Literal string without expression
just a plain value

# Function call
=string.concat($flow.prefix, "-", $flow.name)
```

#### `set-attribute` (alias: `sa`, `sc`, `set-configuration`) — NON-MAPPER CONFIG
Set any non-mapping configuration attribute on any item.

```
# Simple string value
set-attribute flogoFile=<file> selector=<flow>.<activity>.<attrPath> value=<val>

# JSON object value
set-attribute flogoFile=<file> selector=<flow>.<activity>.<attrPath> value='<json>' --jsonValue

# JSON from file
set-attribute flogoFile=<file> selector=<flow>.<activity>.<attrPath> --jsonFile <path>

# Force create if attribute doesn't exist
set-attribute flogoFile=<file> selector=... value=<val> --force
```

**Selector examples:**
```
# Activity attribute
<flow>.ActivityName.input.url
<flow>.ActivityName.input.method
<flow>.ActivityName.input.headers

# Trigger setting
trigger:TriggerName.settings.port
trigger:TriggerName.settings.enableTLS

# Flow metadata (for output schema description)
flow:<flowName>.description
```

**IMPORTANT - Mapper body format via set-attribute:**
When setting a mapper's `input.input` directly (not via make-mapping), the value must be:
```json
{"mapping": {"fieldName": "=expression", "literal": "plainValue"}}
```

#### `set-mapping-schema` (alias: `sms`)
Attach a named JSON schema to an activity input/output for typed-tree resolution in mappings.

```
# Activity input schema
set-mapping-schema flogoFile=<file> selector=<flow>.<activity>.input schema=<schemaName>

# Activity output schema
set-mapping-schema flogoFile=<file> selector=<flow>.<activity>.output schema=<schemaName>

# Both input and output
set-mapping-schema flogoFile=<file> selector=<flow>.<activity>.both schema=<schemaName>

# Flow metadata input/output
set-mapping-schema flogoFile=<file> selector=flow:<flowName>.input schema=<schemaName>
set-mapping-schema flogoFile=<file> selector=flow:<flowName>.output schema=<schemaName>
```

#### `remove-mapping`
Remove a mapping entry by its selector path.
```
remove-mapping flogoFile=<file> selector=<flow>.<activity>.input.input.mapping.<field>
```

#### `remove-mapping-schema`
Detach a schema from an activity.
```
remove-mapping-schema flogoFile=<file> selector=<flow>.<activity>.input
```

#### `list-mappings`
List all currently-set mappings in a flow or activity.
```
list-mappings flogoFile=<file> flow=<flow>
list-mappings flogoFile=<file> flow=<flow> activity=<activity>
```

#### `list-mapping-sources`
List available source values that can map to a target field.
```
list-mapping-sources flogoFile=<file> mappingField=<full-selector>
list-mapping-sources flogoFile=<file> mappingField=<full-selector> filter=activity
list-mapping-sources flogoFile=<file> mappingField=<full-selector> filter=property
list-mapping-sources flogoFile=<file> mappingField=<full-selector> filter=flowctx
```

#### `describe-mapping-fields`
List all mappable fields in the project.
```
describe-mapping-fields flogoFile=<file>
describe-mapping-fields flogoFile=<file> flow=<flow>
```

---

### 3.9 Inspection & Validation

#### `describe-attributes` (alias: `da`)
Inspect any item's attributes using dot-path selector. Essential for discovering what attributes exist before setting them.

```
describe-attributes flogoFile=<file> selector=<flow>.<activity>
describe-attributes flogoFile=<file> selector=<flow>.<activity>.input
describe-attributes flogoFile=<file> selector=trigger:<name>
describe-attributes flogoFile=<file> selector=flow:<name>
```

#### `check` (alias: `ch`, `va`, `validate`) — VALIDATE EXISTENCE
Validate that items exist and have correct configuration values.

```
# Check existence
check flogoFile=<file> type=<itemType> name=<name>

# Check with flow scope
check flogoFile=<file> type=activity flow=<flow> name=<actName>

# Check a specific value
check flogoFile=<file> type=activity flow=<flow> name=<act> \
  attribute=<path> validation=has-correct-value expected=<value>

# Check does NOT exist
check flogoFile=<file> type=activity flow=<flow> name=<act> validation=not-exists
```

**Valid item types:** `project`, `flow`, `activity`, `trigger`, `link`, `schema`, `property`, `configuration`, `connection`, `spec`, `import`, `contribution`

> `check` validates existence and config values ONLY — it does NOT validate mapping expressions. Use `check-mappings` for that.

#### `check-mappings` (alias: `cm`, `validate-mappings`) — VALIDATE EXPRESSIONS
Run comprehensive mapping validation. **Must run after any mapping change.**

```
check-mappings flogoFile=<file>
check-mappings flogoFile=<file> flow=<flow>
check-mappings flogoFile=<file> flow=<flow> activity=<activity>
```

**Validates:**
- `$activity[X]` references point to activities upstream in the flow (not downstream or nonexistent)
- `$property["X"]` references exist as app properties
- All function packages are imported (e.g., `fn_general_string` for `string.*` functions)
- `@foreach` scope references are valid array sources
- `actreturn` keys match the flow's `metadata.output` field names
- REST reply `statusCode` is not zero

---

### 3.10 Discovery Tools

#### `list-activity-types`
List all available activity type references.
```
list-activity-types flogoFile=<file>
list-activity-types flogoFile=<file> filter=<keyword>
```

#### `list-trigger-types`
List all available trigger type references.
```
list-trigger-types flogoFile=<file>
```

#### `list-connection-types`
List all available connection type references.
```
list-connection-types flogoFile=<file>
```

#### `list-functions` (alias: `lf`)
List available Flogo functions with optional category filter. Returns a count.
```
list-functions
list-functions filter=string
list-functions filter=json
list-functions filter=math
```

#### `explain`
Explain a function, activity, connector, or trigger in detail.
```
# Function
explain type=function name=string.concat flogoFile=<file>

# Activity
explain type=activity name=<activityRef> flogoFile=<file>

# Trigger
explain type=trigger name=<triggerRef> flogoFile=<file>
```

---

### 3.11 Testing

#### `create-test-suite`
Create a test suite for a flow.
```
create-test-suite flogoFile=<file> flow=<flow> name=<suiteName>
```

#### `create-test-case`
Create a test case in a suite.
```
create-test-case flogoFile=<file> flow=<flow> suite=<suiteName> name=<caseName>
```

#### `add-test-case-to-suite`
Add an existing test case to a test suite.
```
add-test-case-to-suite flogoFile=<file> flow=<flow> suite=<suiteName> testCase=<caseName>
```

#### `add-assertion`
Add an assertion to a test case.
```
add-assertion flogoFile=<file> flow=<flow> suite=<suiteName> testCase=<caseName> \
  selector=<outputPath> operator=<op> expected=<value>
```

#### `describe-test-file`
Show the test file structure for a Flogo app.
```
describe-test-file flogoFile=<file>
```

---

## 4. COMPLETE FUNCTION REFERENCE

> Import packages are added automatically when you use `make-mapping` with a function. If using `set-attribute` with raw expressions, you may need to `add-import` manually.

### 4.1 `array.*` — Array Operations
Import: `fn_general_array github.com/project-flogo/contrib/function/array`

| Function | Signature | Description |
|----------|-----------|-------------|
| `array.append` | `(items:array, item:any) -> array` | Append item to array |
| `array.contains` | `(array:array, item:any) -> boolean` | True if item exists in array |
| `array.count` | `(items:array) -> int` | Length of array |
| `array.create` | `(item1:any, item2:any) -> array` | Create array from primitive items of same type |
| `array.delete` | `(items:array, index:int) -> array` | Delete item at index |
| `array.flatten` | `(items:array, depth:int) -> array` | Flatten nested arrays to given depth |
| `array.forEach` | `(input:array, scopeName:string, filter:boolean) -> array` | Iterate array; use with @foreach in mappings |
| `array.get` | `(items:array, index:int) -> any` | Get item at index |
| `array.merge` | `(items1:array, items2:array) -> array` | Merge two arrays |
| `array.reverse` | `(items:array) -> array` | Reverse array order |
| `array.slice` | `(items:array, start:int, end:int) -> array` | Extract sub-array (half-open range) |
| `array.sum` | `(items:array) -> float64` | Sum all numeric elements |

### 4.2 `boolean.*` — Boolean Literals
Import: `fn_general_boolean github.com/project-flogo/contrib/function/boolean`

| Function | Signature | Description |
|----------|-----------|-------------|
| `boolean.false` | `() -> boolean` | Always returns false |
| `boolean.not` | `(bool:boolean) -> boolean` | Boolean NOT |
| `boolean.true` | `() -> boolean` | Always returns true |

### 4.3 `coerce.*` — Type Coercion
Import: `fn_general_coerce github.com/project-flogo/contrib/function/coerce`

| Function | Signature | Description |
|----------|-----------|-------------|
| `coerce.toArray` | `(value:any) -> array` | Convert to array |
| `coerce.toBool` | `(value:any) -> bool` | Convert to boolean |
| `coerce.toBytes` | `(value:any) -> bytes` | Convert to bytes |
| `coerce.toFloat32` | `(value:any) -> float32` | Convert to float32 |
| `coerce.toFloat64` | `(value:any) -> float64` | Convert to float64 |
| `coerce.toInt` | `(value:any) -> int` | Convert to int |
| `coerce.toInt32` | `(value:any) -> int32` | Convert to int32 |
| `coerce.toInt64` | `(value:any) -> int64` | Convert to int64 |
| `coerce.toObject` | `(value:any) -> object` | Convert to JSON object |
| `coerce.toParams` | `(value:any) -> params` | Convert to name-value params map |
| `coerce.toString` | `(value:any) -> string` | Convert to string |
| `coerce.toType` | `(value:any, type:string) -> any` | Convert to named type |

### 4.4 `compression.*` — Compression
Import: `fn_general_compression github.com/project-flogo/contrib/function/compression`

| Function | Signature | Description |
|----------|-----------|-------------|
| `compression.gzipCompress` | `(str:string) -> string` | GZip compress a string (e.g. stringified JSON) |
| `compression.gzipUncompress` | `(compressedStr:string) -> string` | GZip decompress a string |

### 4.5 `datetime.*` — Date/Time Operations
Import: `fn_general_datetime github.com/project-flogo/contrib/function/datetime`

| Function | Signature | Description |
|----------|-----------|-------------|
| `datetime.add` | `(datetime, years:int, months:int, days:int) -> datetime` | Add years/months/days |
| `datetime.addHours` | `(datetime, hours:int) -> datetime` | Add hours |
| `datetime.addMins` | `(datetime, mins:int) -> datetime` | Add minutes |
| `datetime.addSeconds` | `(datetime, seconds:int) -> datetime` | Add seconds |
| `datetime.create` | `(years, months, days, hours, ...) -> datetime` | Create datetime |
| `datetime.current` | `() -> datetime` | Current datetime (UTC) as datetime type |
| `datetime.currentDate` | `() -> string` | Current date string (UTC) |
| `datetime.currentDatetime` | `() -> string` | Current datetime string (UTC) |
| `datetime.currentTime` | `() -> string` | Current time string (UTC) |
| `datetime.diff` | `(start:datetime, end:datetime, type:string) -> float64` | Diff in `days`/`hours`/`mins`/`seconds` |
| `datetime.format` | `(datetime, format:string) -> string` | Format datetime; supports `RFC3339`, `RFC822`, `ANSIC`, etc. |
| `datetime.formatDate` | `(datetime, format:string) -> string` | Format date part |
| `datetime.formatDatetime` | `(datetime, format:string) -> string` | Format datetime |
| `datetime.formatTime` | `(datetime, format:string) -> string` | Format time part |
| `datetime.now` | `() -> string` | Current time as UTC string |
| `datetime.parse` | `(str:any, timezone:string) -> datetime` | Parse string to datetime with timezone |
| `datetime.sub` | `(datetime, years:int, months:int, days:int) -> datetime` | Subtract years/months/days |
| `datetime.subHours` | `(datetime, hours:int) -> datetime` | Subtract hours |
| `datetime.subMins` | `(datetime, mins:int) -> datetime` | Subtract minutes |
| `datetime.subSeconds` | `(datetime, seconds:int) -> datetime` | Subtract seconds |

**Format tokens:** `MM`(month), `DD`(day), `YYYY`(year), `hh`(hour), `mm`(min), `ss`(sec) — case-insensitive except `MM`.  
**Predefined layouts:** `ANSIC`, `UnixDate`, `RubyDate`, `RFC822`, `RFC822Z`, `RFC850`, `RFC1123`, `RFC1123Z`, `RFC3339`, `RFC3339Nano`

### 4.6 `float.*` — Float Operations
Import: `fn_general_float github.com/project-flogo/contrib/function/float`

| Function | Signature | Description |
|----------|-----------|-------------|
| `float.float64` | `(input:any, precision:number) -> float64` | Convert to float64 with optional precision |

### 4.7 `json.*` — JSON Operations
Import: `fn_general_json github.com/project-flogo/contrib/function/json`

| Function | Signature | Description |
|----------|-----------|-------------|
| `json.exists` | `(jsonObject:object, key:string) -> bool` | Check if key/JSONPath exists |
| `json.get` | `(jsonObject:object, key:string) -> any` | Get value by key |
| `json.jq` | `(inputJSON:any, query:string) -> array` | JQ-style query, returns filtered array |
| `json.length` | `(jsonObjectOrArray:any) -> int` | Count top-level elements |
| `json.numbersToString` | `(jsonObjectOrArray:any) -> any` | Convert all numbers to strings |
| `json.objKeys` | `(jsonObject:object) -> array` | List all top-level keys |
| `json.objValues` | `(jsonObject:object) -> array` | List all top-level values |
| `json.path` | `(path:string, object:any) -> any` | JSONPath expression (e.g. `$.key`) — see [oliveagle/jsonpath](https://github.com/oliveagle/jsonpath) |
| `json.set` | `(jsonObject:object, key:string, value:any) -> any` | Set/add key in JSON object |

### 4.8 `math.*` — Math Operations
Import: `fn_general_math github.com/project-flogo/contrib/function/math`

| Function | Signature | Description |
|----------|-----------|-------------|
| `math.ceil` | `(inputNumber:number) -> number` | Ceiling (least integer ≥ input) |
| `math.floor` | `(inputNumber:number) -> number` | Floor (greatest integer ≤ input) |
| `math.isNaN` | `(input:any) -> boolean` | Check if IEEE 754 NaN |
| `math.mod` | `(x:number, y:number) -> number` | Floating-point remainder of x/y |
| `math.round` | `(inputNumber:number) -> number` | Round half away from zero |
| `math.roundToEven` | `(inputNumber:number) -> number` | Round ties to even |
| `math.trunc` | `(inputNumber:number) -> number` | Integer value (truncate decimal) |

### 4.9 `number.*` — Number Utilities
Import: `fn_general_number github.com/project-flogo/contrib/function/number`

| Function | Signature | Description |
|----------|-----------|-------------|
| `number.int64` | `(input:any) -> integer` | Convert to int64 |
| `number.len` | `(input:string) -> integer` | String length |
| `number.random` | `(limit:int) -> int` | Random integer from 0 to limit |

### 4.10 `string.*` — String Operations
Import: `fn_general_string github.com/project-flogo/contrib/function/string`

| Function | Signature | Description |
|----------|-----------|-------------|
| `string.base64ToString` | `(base64str:string) -> string` | Decode base64 to string |
| `string.concat` | `(str1, str2, str3, ...) -> string` | Concatenate strings |
| `string.contains` | `(str1:string, str2:string) -> boolean` | Check if str2 in str1 |
| `string.containsAny` | `(str:string, substr:string) -> boolean` | Check if any Unicode char in substr is in str |
| `string.count` | `(str:string, substr:string) -> int` | Count non-overlapping occurrences of substr |
| `string.dateFormat` | `() -> string` | Returns default date format |
| `string.datetimeFormat` | `() -> string` | Returns default datetime format |
| `string.endsWith` | `(str:string, substr:string) -> boolean` | Check suffix |
| `string.equals` | `(str1:string, str2:string) -> boolean` | Case-sensitive equality |
| `string.equalsIgnoreCase` | `(str1:string, str2:string) -> boolean` | Case-insensitive equality |
| `string.float` | `(str1:string, precision:number) -> float64` | Parse string to float |
| `string.index` | `(str:string, substr:string) -> int` | First index of substr (-1 if not found) |
| `string.indexAny` | `(str:string, substr:string) -> int` | First index of any char in substr |
| `string.integer` | `(str1:string) -> int` | Parse string to integer |
| `string.join` | `(items:array, separator:string) -> string` | Join array elements with separator |
| `string.lastIndex` | `(str:string, substr:string) -> int` | Last index of substr (-1 if not found) |
| `string.len` | `(str1:string) -> int` | String length |
| `string.length` | `(str:string) -> integer` | String length (alias) |
| `string.lowerCase` | `(str:string) -> string` | Lowercase |
| `string.matchRegEx` | `(expression:string, str:string) -> boolean` | Regex match |
| `string.regex` | `(pattern:string, str:string) -> boolean` | Regex pattern match |
| `string.replace` | `(str, old, new, count) -> string` | Replace; count<0 = unlimited |
| `string.split` | `(str:string, sep:string) -> array<string>` | Split by separator |
| `string.startsWith` | `(str:string, substr:string) -> boolean` | Check prefix |
| `string.substring` | `(str:string, start:int, end:int) -> string` | Extract substring [start, end) |
| `string.trim` | `(str:string, cutset:string) -> string` | Trim chars in cutset from both ends |
| `string.trimLeft` | `(str:string, cutset:string) -> string` | Trim chars from left |
| `string.trimPrefix` | `(str:string, prefix:string) -> string` | Remove leading prefix |
| `string.trimRight` | `(str:string, cutset:string) -> string` | Trim chars from right |
| `string.trimSuffix` | `(str:string, suffix:string) -> string` | Remove trailing suffix |
| `string.toUpper` | `(str:string) -> string` | Uppercase |
| `string.toLower` | `(str:string) -> string` | Lowercase |
| `string.upperCase` | `(str:string) -> string` | Uppercase (alias) |

### 4.11 `ucs.*` — Universal Condition System
Import: `fn_general_ucs` (UCS expression builder for filter conditions)

| Function | Signature | Description |
|----------|-----------|-------------|
| `ucs.and` | `(leftExpr:object, rightExpr:object) -> object` | AND two expressions |
| `ucs.equal` | `(propName:string, value:any) -> object` | Equality condition `propName == value` |
| `ucs.greaterThan` | `(propName:string, value:any) -> object` | Greater than condition |
| `ucs.greaterThanEqual` | `(propName:string, value:any) -> object` | Greater than or equal |
| `ucs.lessThan` | `(propName:string, value:any) -> object` | Less than condition |
| `ucs.lessThanEqual` | `(propName:string, value:any) -> object` | Less than or equal |
| `ucs.notEqual` | `(propName:string, value:any) -> object` | Not equal condition |
| `ucs.or` | `(leftExpr:object, rightExpr:object) -> object` | OR two expressions |

### 4.12 `url.*` — URL Operations
Import: `fn_general_url` 

| Function | Signature | Description |
|----------|-----------|-------------|
| `url.encode` | `(rawURLString:string) -> string` | URL-encode a string |
| `url.escapedPath` | `(rawURLString:string) -> string` | Extract escaped path |
| `url.hostname` | `(rawURLString:string) -> string` | Extract hostname (no port) |
| `url.path` | `(rawURLString:string) -> string` | Extract path part |
| `url.pathEscape` | `(pathString:string) -> string` | Escape for URL path segment |
| `url.port` | `(rawURLString:string) -> string` | Extract port (no colon) |
| `url.query` | `(rawURLString:string, encode:boolean) -> any` | Extract query string; if encode=false returns object |
| `url.queryEscape` | `(queryValue:string) -> string` | Escape for URL query value |
| `url.scheme` | `(rawURLString:string) -> string` | Extract URL scheme |

### 4.13 `utility.*` — Utility
Import: `fn_general_utility`

| Function | Signature | Description |
|----------|-----------|-------------|
| `utility.renderJSON` | `(data:object, pretty-formatting:boolean) -> string` | Convert JSON object to string |

### 4.14 `utils.*` — Base64 & UUID
Import: `fn_general_utils`

| Function | Signature | Description |
|----------|-----------|-------------|
| `utils.decodeBase64` | `(str:string) -> bytes` | Decode base64 string to bytes |
| `utils.encodeBase64` | `(input:bytes) -> string` | Encode bytes to base64 string |
| `utils.uuid` | `() -> string` | Generate random UUID (RFC 4122) |

---

## 5. COMMON WORKFLOWS

### 5.1 Add a new REST endpoint to an existing app
```
1. create-trigger-handler flogoFile=X trigger=RESTTrigger flow=NewFlow
2. wire-trigger-handler flogoFile=X trigger=RESTTrigger handler=<handlerName>
3. create-activity (add activities to the flow)
4. make-mapping (map inputs/outputs)
5. check-mappings flogoFile=X flow=NewFlow
```

### 5.2 Add a mapper/transform activity
```
1. create-activity flogoFile=X flow=F type=#act_general_mapper name=MapData
2. set-mapping-schema flogoFile=X selector=F.MapData.output schema=OutputSchema
3. make-mapping flogoFile=X selector=F.MapData.input.input.mapping.fieldA value==$flow.inputField
4. check-mappings flogoFile=X flow=F
```

### 5.3 Call a downstream REST service
```
1. create-activity flogoFile=X flow=F type=#act_rest_invoke name=CallService
2. set-attribute flogoFile=X selector=F.CallService.input.url value=http://service/api/v1/resource
3. set-attribute flogoFile=X selector=F.CallService.input.method value=POST
4. set-attribute flogoFile=X selector=F.CallService.input.headers \
     value='{"Content-Type":"application/json","Authorization":"=$property[\"AUTH_HEADER\"]"}' --jsonValue
5. make-mapping flogoFile=X selector=F.CallService.input.input.mapping.body value==$flow.requestBody
6. check-mappings flogoFile=X flow=F
```

### 5.4 Return from a flow with outputs
```
1. create-activity flogoFile=X flow=F type=#act_general_actreturn name=ReturnResult
2. make-mapping flogoFile=X selector=F.ReturnResult.input.input.mapping.status value=success
3. make-mapping flogoFile=X selector=F.ReturnResult.input.input.mapping.data value==$activity[CallService].responseBody
4. check-mappings flogoFile=X flow=F   # validates actreturn keys match flow.metadata.output
```

### 5.5 @foreach (loop over array)
```
# Map array field from upstream
make-mapping flogoFile=X \
  selector=F.ProcessItems.input.input.mapping.items \
  value==$activity[GetItems].responseBody.results \
  --foreach "$activity[GetItems].responseBody.results" --as "item"
```

### 5.6 Discover what attributes an activity has
```
describe-attributes flogoFile=X selector=<flow>.<activity>
describe-attributes flogoFile=X selector=<flow>.<activity>.input
# Then set individual attributes
set-attribute flogoFile=X selector=<flow>.<activity>.input.<attrName> value=<val>
```

---

## 6. ACTIVITY OUTPUT PATHS

| Activity Type | Output Path Pattern |
|--------------|---------------------|
| REST Invoke (`#act_rest_invoke`) | `$activity[Name].responseBody` (object), `$activity[Name].status` (int) |
| Mapper (`#act_general_mapper`) | `$activity[Name].output.<field>` |
| JS Eval (`#act_general_jseval`) | `$activity[Name].result` |
| Log (`#act_general_log`) | no output |
| Iterator | `$activity[Name].output.iteration.<field>` |
| Set Context | `$activity[Name].output.<field>` |
| Get Context | `$activity[Name].output.<field>` |

> **Note**: REST Invoke output is `responseBody` NOT `output.responseBody`. This is a common mistake.

---

## 7. KNOWN CAVEATS & LIMITATIONS

1. **Conditional links**: FDA cannot create them. Edit `.flogo` JSON directly. Always run `check-mappings` after.

2. **remove-link**: Uses internal IDs. If it fails by activity name, edit JSON directly.

3. **Double-serialized JSON**: After `set-attribute --jsonValue`, always verify the resulting value in the `.flogo` file is a proper JSON object, not a string containing escaped JSON.

4. **`flogodesign-cli` path**: Not in PATH. Always use full path: `/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442/bin/flogodesign-cli`

5. **`$activity[X].output.responseBody` vs `$activity[X].responseBody`**: REST Invoke (`#act_rest_invoke`) output is accessed as `$activity[X].responseBody` directly, NOT under `.output`.

6. **Function imports**: Functions require their Go package to be imported in the project. `make-mapping` adds imports automatically. If using raw expressions via `set-attribute`, use `add-import` to add the package manually (e.g. `fn_general_string github.com/project-flogo/contrib/function/string`).

7. **`check` vs `check-mappings`**: `check` only validates existence and config values. It does NOT validate mapping expressions. You must run `check-mappings` separately after any mapping change.

8. **wire-trigger-handler default**: By default, skips fields already wired. Use `--force` to re-wire after schema changes to an already-wired trigger handler.

9. **Mapper input format**: The `input.input` of a mapper activity must be `{"mapping": {"key": "=expr"}}`. If set via `set-attribute`, the `mapping` wrapper is required.
