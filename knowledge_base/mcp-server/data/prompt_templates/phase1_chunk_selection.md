You are planning an evidence investigation of integration source code.

Group: ${GROUP}
Units to investigate (${UNIT_COUNT} total):
${UNIT_LIST}

There are ${CHUNK_COUNT} preprocess chunks available. Select the minimum set needed to cover all units above.

Each chunk entry has:
- unit_names: scanner-detected unit names that appear in this chunk
- categories: evidence categories present (sql_queries, db_connections, auth, etc.)
- identifiers: actual named things extracted from the chunk — SQL table names, class names, connection names, URLs, config keys

Selection strategy:
- A chunk is needed if it contains a unit from the list above (check unit_names)
- Use identifiers to decide between chunks: if you need evidence about a table or class, pick the chunk whose identifiers list contains that name
- Skip chunks where unit_names is empty AND identifiers contains nothing relevant
- When unsure, include the chunk — missing evidence is worse than reading one extra chunk

=== CHUNK MANIFEST ===
${MANIFEST_JSON}
=== END MANIFEST ===

Return ONLY a JSON object: {"fetch_ids": ["id1", "id2"], "reasoning": "one sentence"}
