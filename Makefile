.PHONY: check build
check:
	python3 -m compileall controller.py worker provision
	bash -n provision/install.sh provision/bcpctl
	python3 -m unittest discover -s tests -v
	python3 -c "from pathlib import Path; bad=chr(8212); assert not any(bad in p.read_text(errors='ignore') for p in Path('.').rglob('*') if p.is_file() and '.git' not in p.parts and p.suffix != '.zip')"

build: check
	python3 build.py
