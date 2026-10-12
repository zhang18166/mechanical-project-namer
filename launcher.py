# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import sys


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from mechanical_project_namer.self_test import run
        raise SystemExit(run(sys.argv[2]))
    from mechanical_project_namer.app import main
    raise SystemExit(main())

