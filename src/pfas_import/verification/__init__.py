"""Verification of the published feature table (`pfas_import verify`).

Independent of the pipeline: it reads the import file and the published table itself and shares
no logic with `domain.features` or `pipeline.load` - only configuration and the storage adapters.
"""
