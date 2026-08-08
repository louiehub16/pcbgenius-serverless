"""PCBGenius — E3 Self-Improving Flywheel package.

Pipeline: capture.py (log every design + 4D verdict + fix) ->
          curate.py  (dedupe / drop junk -> clean training pairs) ->
          export.py  (Phase-2 training-dataset format rows).
"""
__version__ = "0.1.0"