// Board state for one signed-in session.
//
// Holds the gig list, the inbox and the agent directory, and keeps them fresh
// with a long-poll loop on `/v1/events`. Screens listen to it and read
// [revisionOf] to know when an open thread should reload.

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import 'api.dart';
import 'models.dart';

enum LiveState { connecting, live, retrying }

class Board extends ChangeNotifier {
  Board({required this.api, required Me me})
      : _me = me,
        _boot = me.boot,
        _next = me.seq;

  final ApiClient api;

  Me _me;
  Me get me => _me;

  List<Gig> gigs = const [];
  InboxData inbox = InboxData.empty;
  AgentDirectory directory = AgentDirectory.empty;

  bool loadingLists = true;
  String? listError;
  LiveState live = LiveState.connecting;

  String _boot;
  int _next;
  bool _running = false;
  bool _disposed = false;
  Timer? _clock;

  /// Bumped for every event that touches a gig; [_epoch] bumps for all.
  final Map<String, int> _revisions = {};
  int _epoch = 0;

  int revisionOf(String gigId) => _epoch + (_revisions[gigId] ?? 0);

  /// Every agent id and group key a new conversation can be sent to.
  List<String> get recipients {
    final ids = <String>[for (final a in directory.agents) a.id];
    for (final g in directory.groups.keys) {
      if (!ids.contains(g)) ids.add(g);
    }
    return ids;
  }

  /// Agent ids that belong to group [key] (empty for a plain agent id).
  List<String> membersOf(String key) => directory.groups[key] ?? const [];

  Agent? agent(String id) {
    for (final a in directory.agents) {
      if (a.id == id) return a;
    }
    return null;
  }

  // ---------------------------------------------------------------- loading

  /// Loads the lists and starts the live loop. [me] was just fetched, so it
  /// is not fetched again here.
  void start() {
    _running = true;
    // Repaint once a minute so relative times ("5 min ago") stay true.
    _clock = Timer.periodic(const Duration(minutes: 1), (_) => _notify());
    unawaited(refreshLists());
    unawaited(refreshAgents());
    unawaited(_loop());
  }

  /// Reloads everything, including who the person is.
  Future<void> refreshAll() async {
    try {
      final me = await api.me();
      if (_disposed) return;
      _me = me;
    } catch (_) {
      // Keep the previous identity; the lists below report their own errors.
    }
    await Future.wait([refreshLists(), refreshAgents()]);
  }

  Future<void> refreshLists() async {
    try {
      final results = await Future.wait([api.gigList(limit: 200), api.inbox()]);
      if (_disposed) return;
      final list = results[0] as List<Gig>;
      list.sort(Gig.byNewest);
      gigs = list;
      inbox = results[1] as InboxData;
      listError = null;
    } catch (e) {
      listError = e is ApiException ? e.message : 'Could not load the board.';
    } finally {
      loadingLists = false;
      _notify();
    }
  }

  Future<void> refreshAgents() async {
    try {
      final dir = await api.agentList();
      if (_disposed) return;
      directory = dir;
      _notify();
    } catch (_) {
      // The agents screen shows whatever was loaded last.
    }
  }

  // ------------------------------------------------------------- live feed

  Future<void> _loop() async {
    var backoff = 2;
    while (_running && !_disposed) {
      try {
        final batch = await api.events(after: _next, boot: _boot);
        if (!_running || _disposed) return;
        backoff = 2;
        _setLive(LiveState.live);

        final bootChanged = batch.boot.isNotEmpty && _boot.isNotEmpty && batch.boot != _boot;
        if (batch.boot.isNotEmpty) _boot = batch.boot;
        if (batch.next != null) {
          _next = batch.next!;
        } else if (batch.events.isNotEmpty) {
          _next = batch.events.map((e) => e.seq).reduce(math.max);
        }

        if (batch.gap || bootChanged) {
          _epoch++;
          await refreshAll();
        } else if (batch.events.isNotEmpty) {
          for (final e in batch.events) {
            final id = e.gigId;
            if (id != null) _revisions[id] = (_revisions[id] ?? 0) + 1;
          }
          await refreshLists();
          if (batch.events.any((e) => e.type.startsWith('agent'))) await refreshAgents();
        }
      } catch (_) {
        if (!_running || _disposed) return;
        _setLive(LiveState.retrying);
        await Future<void>.delayed(Duration(seconds: backoff));
        backoff = math.min(backoff * 2, 30);
      }
    }
  }

  void _setLive(LiveState s) {
    if (live == s) return;
    live = s;
    _notify();
  }

  /// Marks a gig as changed by this client (after a send), so open threads
  /// reload without waiting for the event feed.
  void touch(String gigId) {
    _revisions[gigId] = (_revisions[gigId] ?? 0) + 1;
    unawaited(refreshLists());
  }

  void stop() {
    _running = false;
    _clock?.cancel();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _running = false;
    _disposed = true;
    _clock?.cancel();
    api.close();
    super.dispose();
  }
}
