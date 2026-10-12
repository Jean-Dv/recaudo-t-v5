PYTHON ?= python3

.PHONY: run test quick evidence report-md report clean-runtime

run:
	$(PYTHON) -m recaudot system

test:
	$(PYTHON) -m unittest discover -v

quick:
	$(PYTHON) -m recaudot experiment all --quick

evidence:
	$(PYTHON) -m recaudot experiment all

report-md:
	$(PYTHON) -m recaudot.report

report: report-md
	pandoc docs/part-2-report.md -t ms | groff -k -t -ms -Tps | ps2pdf - docs/part-2-report.pdf

clean-runtime:
	$(PYTHON) -c "import shutil; shutil.rmtree('runtime', ignore_errors=True)"
