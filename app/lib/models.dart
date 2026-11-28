// Data models for the sidegig API.
//
// Parsing is forgiving on purpose: missing or unexpected fields become empty
// values instead of exceptions, so a newer server never breaks the app.

String _str(Object? v) => v == null ? '' : v.toString();

String? _strOrNull(Object? v) {
  if (v == null) return null;
  final s = v.toString();
  return s.isEmpty ? null : s;
}

List<String> _strList(Object? v) =>
    v is List ? v.where((e) => e != null).map((e) => e.toString()).toList() : const [];

Map<String, dynamic> _map(Object? v) =>
    v is Map ? v.map((k, val) => MapEntry(k.toString(), val)) : <String, dynamic>{};

int _int(Object? v, [int fallback = 0]) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(_str(v)) ?? fallback;
}

/// Parses ISO times ("2026-11-22T14:57:10Z") and the short thread form
/// ("2026-11-22 14:57"). Times without a zone are treated as UTC.
DateTime? parseTime(String? raw) {
  if (raw == null || raw.trim().isEmpty) return null;
  var s = raw.trim();
  if (RegExp(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$').hasMatch(s)) {
    s = '${s.replaceFirst(' ', 'T')}Z';
  }
  return DateTime.tryParse(s)?.toLocal();
}

/// The signed-in person as the server sees them (`GET /v1/me`).
class Me {
  Me({
    required this.agent,
    required this.team,
    required this.teamName,
    required this.role,
    required this.groups,
    required this.boot,
    required this.seq,
    required this.email,
    required this.name,
    required this.canActAs,
  });

  final String agent;
  final String team;
  final String teamName;
  final String role;
  final List<String> groups;
  final String boot;
  final int seq;
  final String email;
  final String name;

  /// Agent ids this person may speak as. Always starts with [agent].
  final List<String> canActAs;

  factory Me.fromJson(Map<String, dynamic> j) {
    final agent = _str(j['agent']);
    final person = _map(j['person']);
    final acts = <String>[if (agent.isNotEmpty) agent];
    for (final a in _strList(j['can_act_as'])) {
      if (!acts.contains(a)) acts.add(a);
    }
    return Me(
      agent: agent,
      team: _str(j['team']),
      teamName: _str(j['team_name']),
      role: _str(j['role']),
      groups: _strList(j['groups']),
      boot: _str(j['boot']),
      seq: _int(j['seq']),
      email: _str(person['email']),
      name: _str(person['name']),
      canActAs: acts,
    );
  }

  /// Whether [id] is the person or one of the agents they may act as.
  bool owns(String? id) => id != null && id.isNotEmpty && canActAs.contains(id);
}

/// One gig (a conversation) on the board.
class Gig {
  Gig({
    required this.id,
    required this.title,
    required this.status,
    required this.tags,
    required this.asker,
    required this.toAgent,
    required this.claimedBy,
    required this.created,
    required this.updated,
  });

  final String id;
  final String title;
  final String status;
  final List<String> tags;
  final String asker;
  final String toAgent;
  final String? claimedBy;
  final String created;
  final String updated;

  DateTime? get updatedAt => parseTime(updated) ?? parseTime(created);

  bool get isClosed => status == 'closed';

  factory Gig.fromJson(Map<String, dynamic> j) => Gig(
        id: _str(j['id'] ?? j['gig_id']),
        title: _str(j['title']).isEmpty ? '(untitled)' : _str(j['title']),
        status: _str(j['status']).isEmpty ? 'open' : _str(j['status']),
        tags: _strList(j['tags']),
        asker: _str(j['asker']),
        toAgent: _str(j['to_agent']),
        claimedBy: _strOrNull(j['claimed_by']),
        created: _str(j['created']),
        updated: _str(j['updated']),
      );

  static List<Gig> listFrom(Object? v) => v is List
      ? v.whereType<Map>().map((m) => Gig.fromJson(_map(m))).where((g) => g.id.isNotEmpty).toList()
      : <Gig>[];

  /// Newest activity first.
  static int byNewest(Gig a, Gig b) {
    final ta = a.updatedAt, tb = b.updatedAt;
    if (ta == null && tb == null) return 0;
    if (ta == null) return 1;
    if (tb == null) return -1;
    return tb.compareTo(ta);
  }
}

/// One entry of a thread: a message, an answer, or a status line.
class ThreadMessage {
  ThreadMessage({
    required this.kind,
    required this.author,
    required this.agentId,
    required this.when,
    required this.text,
  });

  final String kind;
  final String author;
  final String? agentId;
  final String when;
  final String text;

  bool get isStatus => kind == 'status';
  bool get isAnswer => kind == 'answer';

  /// The agent id when the server knows it, else the display author.
  String get speaker => agentId ?? author;

  DateTime? get time => parseTime(when);

  factory ThreadMessage.fromJson(Map<String, dynamic> j) => ThreadMessage(
        kind: _str(j['kind']).isEmpty ? 'message' : _str(j['kind']),
        author: _str(j['author']),
        agentId: _strOrNull(j['agent_id']),
        when: _str(j['when']),
        text: _str(j['text']),
      );
}

/// A gig with its messages (`gig_read` with `as_messages`).
class GigThread {
  GigThread({required this.meta, required this.messages, required this.open, required this.answers});

  final Gig meta;
  final List<ThreadMessage> messages;
  final bool open;
  final int answers;

  /// The agent that wrote the most recent answer, if any.
  String? get lastAnswerer {
    for (final m in messages.reversed) {
      if (m.isAnswer) return m.speaker;
    }
    return null;
  }

  factory GigThread.fromJson(Map<String, dynamic> j) {
    final msgs = j['messages'] is List
        ? (j['messages'] as List).whereType<Map>().map((m) => ThreadMessage.fromJson(_map(m))).toList()
        : <ThreadMessage>[];
    return GigThread(
      meta: Gig.fromJson(_map(j['meta'])),
      messages: msgs,
      open: j['open'] == true,
      answers: _int(j['answers']),
    );
  }
}

/// An agent registered on the board.
class Agent {
  Agent({
    required this.id,
    required this.name,
    required this.owner,
    required this.skills,
    required this.description,
    required this.lastSeen,
    required this.online,
  });

  final String id;
  final String name;
  final String owner;
  final List<String> skills;
  final String description;
  final String lastSeen;

  /// Null when the server does not report presence.
  final bool? online;

  DateTime? get lastSeenAt => parseTime(lastSeen);

  factory Agent.fromJson(Map<String, dynamic> j) {
    final id = _str(j['id']);
    return Agent(
      id: id,
      name: _str(j['name']).isEmpty ? id : _str(j['name']),
      owner: _str(j['owner']),
      skills: _strList(j['skills']),
      description: _str(j['description']),
      lastSeen: _str(j['last_seen']),
      online: j['online'] is bool ? j['online'] as bool : null,
    );
  }
}

/// Agents plus the named groups they belong to (`agent_list`).
class AgentDirectory {
  AgentDirectory({required this.agents, required this.groups});

  final List<Agent> agents;
  final Map<String, List<String>> groups;

  static final empty = AgentDirectory(agents: const [], groups: const {});

  factory AgentDirectory.fromJson(Map<String, dynamic> j) {
    final agents = j['agents'] is List
        ? (j['agents'] as List).whereType<Map>().map((m) => Agent.fromJson(_map(m))).toList()
        : <Agent>[];
    agents.sort((a, b) => a.id.compareTo(b.id));
    final groups = <String, List<String>>{};
    _map(j['groups']).forEach((k, v) => groups[k] = _strList(v));
    return AgentDirectory(agents: agents, groups: groups);
  }
}

/// What needs the person now (`inbox`).
class InboxData {
  InboxData({required this.toAnswer, required this.answered, required this.other});

  final List<Gig> toAnswer;

  /// Gigs the person asked that have an answer waiting.
  final List<Gig> answered;

  /// Any other list-valued keys the server returns, by key.
  final Map<String, List<Gig>> other;

  static final empty = InboxData(toAnswer: const [], answered: const [], other: const {});

  factory InboxData.fromJson(Map<String, dynamic> j) {
    final other = <String, List<Gig>>{};
    j.forEach((k, v) {
      if (k == 'to_answer' || k == 'answered' || v is! List) return;
      other[k] = Gig.listFrom(v);
    });
    return InboxData(
      toAnswer: Gig.listFrom(j['to_answer']),
      answered: Gig.listFrom(j['answered']),
      other: other,
    );
  }
}

/// One change on the board, from the live event feed.
class BoardEvent {
  BoardEvent({required this.seq, required this.type, required this.gigId});

  final int seq;
  final String type;
  final String? gigId;

  factory BoardEvent.fromJson(Map<String, dynamic> j) =>
      BoardEvent(seq: _int(j['seq']), type: _str(j['type']), gigId: _strOrNull(j['gig_id']));
}

/// One reply of the long-poll feed (`GET /v1/events`).
class EventBatch {
  EventBatch({required this.boot, required this.events, required this.gap, required this.next});

  final String boot;
  final List<BoardEvent> events;
  final bool gap;
  final int? next;

  factory EventBatch.fromJson(Map<String, dynamic> j) => EventBatch(
        boot: _str(j['boot']),
        events: j['events'] is List
            ? (j['events'] as List).whereType<Map>().map((m) => BoardEvent.fromJson(_map(m))).toList()
            : <BoardEvent>[],
        gap: j['gap'] == true,
        next: j['next'] == null ? null : _int(j['next']),
      );
}
