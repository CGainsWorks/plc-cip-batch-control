# PLCopen XML export

[export/plcopen/cip_skid.xml](../export/plcopen/cip_skid.xml) is the whole of
`plc/` in PLCopen TC6 XML, version 2.01, the exchange format CODESYS, TwinCAT
and most other IEC 61131-3 environments can import. It is generated:

```bash
python scripts/export_plcopen.py            # or: make plcopen
```

## What is in it

* **Data types.** The six enumerations with their explicit values and the six
  structures with their member initial values.
* **POUs.** Six function blocks, four functions and five programs. Interfaces
  are written out variable by variable (`inputVars`, `outputVars`,
  `inOutVars`, `localVars`, with `constant="true"` where the source says
  `CONSTANT`). Bodies are Structured Text, copied verbatim, comments included.
* **One configuration**, `CipSkid`, with one resource and one cyclic task,
  `MainTask`, at `T#200ms`, calling `PRG_Safety`, `PRG_Sequence`,
  `PRG_Control`, `PRG_Alarms` and `PRG_Kpi` in that order. That order is part
  of the design: see the header comment in `PRG_Safety.st`.
* **The three global variable lists** as `globalVars` on the configuration,
  with every `AT %I…`, `%Q…` and `%M…` address carried as the variable's
  `address` attribute.

## How it is checked

All of these run in CI on every push:

1. The committed file is regenerated and compared byte for byte, so it cannot
   drift from `plc/`. The creation time in the header comes from
   `SOURCE_DATE_EPOCH` (default 0, so 1970-01-01) to keep the file
   reproducible; it is not the date of the export.
2. It validates against the TC6 v2.01 XML schema (`tc6_xml_v201.xsd`, taken
   from the Beremiz repository at a pinned commit and checked by SHA-256)
   with `xmllint`.
3. It is read back into `.st` files. Those load in the interpreter with the
   same 134 globals and 66 located variables at the same addresses. They
   compile with no diagnostics at all under RuSTy, the same compiler that
   builds `plc/` in the `rusty` job.

## What has not been done

**The file has not been imported into CODESYS, TwinCAT or any other vendor
environment.** Neither is available here. Schema-valid TC6 XML is necessary
for an import and not sufficient: each vendor reads the format with its own
extensions and omissions. What to expect on a first import, from the format
rather than from a trial:

* CODESYS keeps global variable lists as separate objects and builds its task
  configuration itself; the configuration and task here may be ignored or
  need recreating. The call order of the five programs must then be set
  by hand.
* TwinCAT imports POUs and data types into a PLC project but takes the task
  from the TwinCAT system configuration, not from the XML.
* Located variables: both accept `AT %IX0.0` syntax, though a TwinCAT project
  would normally declare `AT %I*` and link the variable to a terminal in the
  system configuration. Either way the mapping of addresses onto real I/O
  modules is device configuration and is not in this file.
* `TimeToSeconds` and `SecondsToTime` use `TIME_TO_LREAL` and `LREAL_TO_TIME`,
  which both vendors document. They divide by the same conversion of `T#1s`,
  so they give seconds whatever unit the vendor's conversion counts in.

Treat the file as a starting point for a vendor project, to be compiled by
that vendor's toolchain and checked, never as a substitute for doing so.
