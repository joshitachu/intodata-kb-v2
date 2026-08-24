You are extracting structured evidence from integration scan data.

Scan data for group: ${GROUP}
Source file: ${SOURCE_REF}

=== SCAN DATA ===
${CONTENT}
=== END SCAN DATA ===

Extract evidence for EVERY unit listed below from the scan data above.
If a unit truly has no data, add it to skipped_units with a specific reason.

Units to cover:
${UNIT_LIST}

Return ONLY a valid JSON object (no markdown fences, no explanation):

{
  "evidence": [
    {
      "unit_name": "exact unit name from the list above",
      "category": "short label describing the evidence type (e.g. data flow, business logic, error handling, security, orchestration)",
      "source_file": "${SOURCE_REF}",
      "chunk_ref": "${SOURCE_REF}",
      "line_range": "approximate line range e.g. 10-25",
      "snippet": "exact short excerpt from the scan data (minimum 8 chars)",
      "observation": "specific concrete finding about this unit (minimum 20 chars)"
    }
  ],
  "skipped_units": {
    "UnitName": "specific reason this unit has no usable data in the scan"
  }
}

Rules:
- Every unit must appear in evidence OR skipped_units
- snippet must be an exact excerpt from the scan data above
- observation must be specific, not generic phrases like "needs review" or "no details available"
- chunk_ref must be exactly: ${SOURCE_REF}
- source_file may be ${SOURCE_REF} unless the scan data names a more specific original source artifact
- line_range is required (approximate is fine)
- Multiple evidence items per unit are encouraged
- If the scan data is a pure data file (JSON/XML data without source code, logic, or
  configuration values — e.g. a fixture file, test dataset, or empty schema stub),
  list ALL units in skipped_units with reason "data-only file: no source code in scanner output"
