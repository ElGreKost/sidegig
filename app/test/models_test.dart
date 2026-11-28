// Parsing checks for the API models (no network, no Firebase).

import 'package:flutter_test/flutter_test.dart';
import 'package:sidegig_app/models.dart';

void main() {
  test('Me puts its own agent first in canActAs', () {
    final me = Me.fromJson({
      'ok': true,
      'agent': 'alice',
      'boot': 'b1',
      'seq': 4,
      'person': {'name': 'Alice'},
      'can_act_as': ['alice-dev', 'alice'],
    });
    expect(me.canActAs, ['alice', 'alice-dev']);
    expect(me.owns('alice-dev'), isTrue);
    expect(me.owns('bob'), isFalse);
  });

  test('GigThread finds the last answerer and keeps status lines', () {
    final t = GigThread.fromJson({
      'meta': {'id': 'g1', 'title': 'Hello', 'status': 'answered', 'asker': 'alice', 'to_agent': 'bob'},
      'messages': [
        {'kind': 'message', 'author': 'alice', 'agent_id': 'alice', 'when': '2026-01-02 10:00', 'text': 'Hi'},
        {'kind': 'status', 'author': 'bob', 'agent_id': null, 'when': '2026-01-02 10:01', 'text': 'claimed'},
        {'kind': 'answer', 'author': 'Bob', 'agent_id': 'bob', 'when': '2026-01-02 10:02', 'text': 'Hello'},
      ],
      'open': false,
      'answers': 1,
    });
    expect(t.messages.length, 3);
    expect(t.messages[1].isStatus, isTrue);
    expect(t.lastAnswerer, 'bob');
    expect(t.messages.first.time, isNotNull);
  });

  test('Inbox keeps extra list keys and ignores scalars', () {
    final inbox = InboxData.fromJson({
      'to_answer': [
        {'id': 'g1', 'title': 'A'},
      ],
      'answered': <Object>[],
      'held': [
        {'id': 'g2', 'title': 'B'},
      ],
      'unaddressed_open': 3,
    });
    expect(inbox.toAnswer.single.id, 'g1');
    expect(inbox.other.keys, ['held']);
  });
}
