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

Return ONLY a valid JSON object:

{
  "evidence": [
    {
      "unit_name": "exact unit name from the list above",
      "category": "short label (e.g. data flow, business logic, error handling)",
      "source_file": "${SOURCE_REF}",
      "chunk_ref": "${SOURCE_REF}",
      "line_range": "approximate line range e.g. 10-25",
      "snippet": "exact short excerpt from the scan data (minimum 8 chars)",
      "observation": "specific concrete finding about this unit (minimum 20 chars)"
    }
  ],
  "skipped_units": {"UnitName": "specific reason"}
}

Rules:
- Every unit must appear in evidence OR skipped_units
- snippet must be an exact excerpt from the scan data above
- observation must be specific, not generic
- chunk_ref must be exactly: ${SOURCE_REF}
- line_range is required (approximate is fine)
- Multiple evidence items per unit are encouraged
