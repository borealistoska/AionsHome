"""AGY PreToolUse policy: approve only the current turn's staged images."""

import json
import os
import sys
from pathlib import Path


def image_permission_decision(event, image_paths):
    call = event.get('toolCall') or {}
    path = (call.get('args') or {}).get('AbsolutePath')
    allowed = {os.path.normcase(str(Path(item).resolve())) for item in image_paths}
    if call.get('name') == 'view_file' and isinstance(path, str) and path:
        if os.path.normcase(str(Path(path).resolve())) in allowed:
            # CLI 1.2.16 still consumes the legacy allowTool/denyReason fields.
            return {'decision': 'allow', 'allowTool': True}
    reason = 'Only the current chat image attachments may be read.'
    return {'decision': 'deny', 'allowTool': False, 'reason': reason, 'denyReason': reason}


if __name__ == '__main__':
    try:
        event = json.load(sys.stdin)
        image_paths = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        decision = image_permission_decision(event, image_paths)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        reason = 'Image attachment permissions could not be verified.'
        decision = {'decision': 'deny', 'allowTool': False, 'reason': reason, 'denyReason': reason}
    print(json.dumps(decision))
