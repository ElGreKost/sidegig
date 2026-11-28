// Thread: one conversation as a chat, plus the composer.
//
// Who may write depends on the "Speak as" choice:
//   the gig's asker: follow-up (gig_followup), and may close it (gig_close)
//   the agent it was sent to (or a member of that group), the holder, or the
//   last answerer: reply (gig_say)
//   anyone else: composer disabled with a hint
// All text is plain: never parsed as HTML or Markdown, links never opened.

import 'dart:math' as math;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../board.dart';
import '../models.dart';
import '../widgets.dart';

/// The thread as a page of its own, for phones.
class ThreadPage extends StatelessWidget {
  const ThreadPage({super.key, required this.board, required this.gigId});

  final Board board;
  final String gigId;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: ThreadView(board: board, gigId: gigId, leading: const BackButton()),
        ),
      );
}

enum _WriteMode { asker, answerer, none }

class ThreadView extends StatefulWidget {
  const ThreadView({super.key, required this.board, required this.gigId, this.leading});

  final Board board;
  final String gigId;
  final Widget? leading;

  @override
  State<ThreadView> createState() => _ThreadViewState();
}

class _ThreadViewState extends State<ThreadView> {
  final _text = TextEditingController();
  final _focus = FocusNode();

  GigThread? _thread;
  ApiException? _error;
  bool _loading = true;
  bool _inFlight = false;
  bool _reloadAgain = false;
  bool _sending = false;
  late int _seenRevision;
  late String _speaker;
  bool _speakerPicked = false; // the person chose in Speak as: never override it

  Board get board => widget.board;

  @override
  void initState() {
    super.initState();
    _speaker = board.me.agent;
    _seenRevision = board.revisionOf(widget.gigId);
    board.addListener(_onBoardChanged);
    _load();
  }

  @override
  void dispose() {
    board.removeListener(_onBoardChanged);
    _text.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _onBoardChanged() {
    final rev = board.revisionOf(widget.gigId);
    if (rev != _seenRevision) {
      _seenRevision = rev;
      _load(quiet: true);
    }
    if (!board.me.canActAs.contains(_speaker) && mounted) {
      setState(() => _speaker = board.me.agent);
    }
  }

  /// Loads the thread. Overlapping calls collapse into one extra reload.
  Future<void> _load({bool quiet = false}) async {
    if (_inFlight) {
      _reloadAgain = true;
      return;
    }
    _inFlight = true;
    if (!quiet && mounted) setState(() => _loading = true);
    try {
      final t = await board.api.gigRead(widget.gigId);
      if (!mounted) return;
      setState(() {
        _thread = t;
        _error = null;
        if (!_speakerPicked && _modeFor(t) == _WriteMode.none) {
          // One of the person's own agents asked or was asked: speak as that one.
          _speaker = board.me.canActAs.firstWhere(
              (id) => _modeFor(t, id) != _WriteMode.none,
              orElse: () => _speaker);
        }
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      if (_thread == null) {
        setState(() => _error = e);
      } else if (!quiet) {
        showError(context, e);
      }
    } finally {
      _inFlight = false;
      if (mounted && _loading) setState(() => _loading = false);
      if (_reloadAgain && mounted) {
        _reloadAgain = false;
        _load(quiet: true);
      }
    }
  }

  _WriteMode _modeFor(GigThread t, [String? as]) {
    final meta = t.meta;
    final who = as ?? _speaker;
    if (who == meta.asker) return _WriteMode.asker;
    if (who == meta.toAgent ||
        who == t.lastAnswerer ||
        who == meta.claimedBy ||
        board.membersOf(meta.toAgent).contains(who)) {
      return _WriteMode.answerer;
    }
    return _WriteMode.none;
  }

  String? get _actAs => _speaker == board.me.agent ? null : _speaker;

  Future<void> _send() async {
    final t = _thread;
    final text = _text.text.trim();
    if (t == null || text.isEmpty || _sending) return;
    final mode = _modeFor(t);
    if (mode == _WriteMode.none) return;
    setState(() => _sending = true);
    try {
      if (mode == _WriteMode.asker) {
        await board.api.gigFollowup(widget.gigId, text, actAs: _actAs);
      } else {
        await board.api.gigSay(widget.gigId, text, actAs: _actAs);
      }
      _text.clear();
      _seenRevision = board.revisionOf(widget.gigId) + 1;
      board.touch(widget.gigId);
      await _load(quiet: true);
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) {
        setState(() => _sending = false);
        _focus.requestFocus();
      }
    }
  }

  Future<void> _close() async {
    final note = await showDialog<String>(context: context, builder: (_) => const _CloseDialog());
    if (note == null || !mounted) return;
    try {
      await board.api.gigClose(widget.gigId, note: note, actAs: _actAs);
      _seenRevision = board.revisionOf(widget.gigId) + 1;
      board.touch(widget.gigId);
      await _load(quiet: true);
      if (mounted) showNote(context, 'Conversation closed.');
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = _thread;

    Widget body;
    if (t == null && _loading) {
      body = const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)));
    } else if (t == null) {
      body = EmptyState(
        icon: Icons.error_outline,
        title: 'Could not open this conversation',
        body: _error?.message,
        action: OutlinedButton(onPressed: _load, child: const Text('Try again')),
      );
    } else if (t.messages.isEmpty) {
      body = const EmptyState(icon: Icons.chat_bubble_outline, title: 'No messages yet');
    } else {
      body = _messages(t);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _Header(gig: t?.meta, leading: widget.leading, onRefresh: () => _load()),
        Divider(color: cs.outlineVariant),
        Expanded(child: body),
        if (t != null) _composer(t),
      ],
    );
  }

  Widget _messages(GigThread t) {
    final msgs = t.messages;
    return LayoutBuilder(builder: (context, c) {
      final bubbleMax = math.min(620.0, c.maxWidth * 0.86);
      return SelectionArea(
        child: ListView.builder(
          reverse: true,
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
          itemCount: msgs.length,
          itemBuilder: (context, i) {
            final m = msgs[msgs.length - 1 - i];
            if (m.isStatus) return _StatusLine(message: m);
            return _Bubble(message: m, mine: board.me.owns(m.speaker), maxWidth: bubbleMax);
          },
        ),
      );
    });
  }

  Widget _composer(GigThread t) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final mode = _modeFor(t);
    final me = board.me;

    final speakAs = Row(
      children: [
        Text('Speak as', style: theme.textTheme.labelMedium?.copyWith(color: cs.onSurfaceVariant)),
        const SizedBox(width: 8),
        Flexible(
          child: DropdownButton<String>(
            value: me.canActAs.contains(_speaker) ? _speaker : null,
            isDense: true,
            isExpanded: false,
            underline: const SizedBox.shrink(),
            borderRadius: BorderRadius.circular(12),
            style: theme.textTheme.labelLarge?.copyWith(color: cs.onSurface),
            items: [
              for (final id in me.canActAs)
                DropdownMenuItem(value: id, child: Text(id, overflow: TextOverflow.ellipsis)),
            ],
            onChanged: _sending
                ? null
                : (v) => setState(() {
                      _speakerPicked = true;
                      _speaker = v ?? me.agent;
                    }),
          ),
        ),
        const Spacer(),
        if (mode == _WriteMode.asker && !t.meta.isClosed)
          TextButton.icon(
            onPressed: _sending ? null : _close,
            icon: const Icon(Icons.check_circle_outline, size: 18),
            label: const Text('Close'),
          ),
      ],
    );

    final Widget input;
    if (mode == _WriteMode.none) {
      input = Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: cs.surfaceContainerLow,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: cs.outlineVariant),
        ),
        child: Text(
          'Only the asker or the agent it was sent to can write here. '
          'Pick one of yours in Speak as, or start a new conversation.',
          style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant),
        ),
      );
    } else {
      input = Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Focus(
              onKeyEvent: _onKey,
              child: TextField(
                controller: _text,
                focusNode: _focus,
                enabled: !_sending,
                minLines: 1,
                maxLines: 6,
                keyboardType: TextInputType.multiline,
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(
                  hintText: mode == _WriteMode.asker ? 'Write a follow-up' : 'Write a reply',
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          IconButton.filled(
            onPressed: _sending ? null : _send,
            tooltip: kIsWeb ? 'Send (Enter). Shift+Enter adds a new line.' : 'Send',
            style: IconButton.styleFrom(
              minimumSize: const Size(46, 46),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            ),
            icon: _sending
                ? SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary),
                  )
                : const Icon(Icons.arrow_upward),
          ),
        ],
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border(top: BorderSide(color: cs.outlineVariant)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 14),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [speakAs, const SizedBox(height: 8), input],
        ),
      ),
    );
  }

  /// On a hardware keyboard, Enter sends and Shift+Enter adds a new line.
  KeyEventResult _onKey(FocusNode node, KeyEvent e) {
    if (!kIsWeb || e is! KeyDownEvent) return KeyEventResult.ignored;
    final enter = e.logicalKey == LogicalKeyboardKey.enter || e.logicalKey == LogicalKeyboardKey.numpadEnter;
    if (!enter || HardwareKeyboard.instance.isShiftPressed) return KeyEventResult.ignored;
    _send();
    return KeyEventResult.handled;
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.gig, required this.leading, required this.onRefresh});

  final Gig? gig;
  final Widget? leading;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final g = gig;
    final claimed = g?.claimedBy;
    return Padding(
      padding: EdgeInsets.fromLTRB(leading == null ? 20 : 4, 12, 8, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (leading != null) Padding(padding: const EdgeInsets.only(right: 4), child: leading),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(top: leading == null ? 0 : 8),
              child: g == null
                  ? const SizedBox(height: 40)
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SelectableText(g.title, style: theme.textTheme.titleMedium?.copyWith(height: 1.3)),
                        const SizedBox(height: 6),
                        Wrap(
                          spacing: 10,
                          runSpacing: 6,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            RouteLine(from: g.asker, to: g.toAgent),
                            StatusChip(g.status),
                            if (claimed != null && claimed != g.toAgent)
                              Text('held by $claimed',
                                  style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                          ],
                        ),
                      ],
                    ),
            ),
          ),
          IconButton(
            tooltip: 'Reload',
            onPressed: onRefresh,
            icon: Icon(Icons.refresh, size: 20, color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message, required this.mine, required this.maxWidth});

  final ThreadMessage message;
  final bool mine;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final m = message;
    final bg = mine ? cs.secondaryContainer : cs.surfaceContainerLow;
    final fg = mine ? cs.onSecondaryContainer : cs.onSurface;
    const r = Radius.circular(16);
    const tight = Radius.circular(5);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Align(
        alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth),
          child: Column(
            crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(m.author.isEmpty ? m.speaker : m.author,
                        style: theme.textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w600)),
                    if (m.isAnswer)
                      Text('answer',
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: cs.primary,
                            fontWeight: FontWeight.w600,
                          )),
                    Text(clockTime(m.time, m.when),
                        style: theme.textTheme.labelSmall?.copyWith(color: cs.onSurfaceVariant)),
                  ],
                ),
              ),
              const SizedBox(height: 4),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                decoration: BoxDecoration(
                  color: bg,
                  border: m.isAnswer ? Border.all(color: cs.primary.withValues(alpha: 0.35)) : null,
                  borderRadius: BorderRadius.only(
                    topLeft: mine ? r : tight,
                    topRight: mine ? tight : r,
                    bottomLeft: r,
                    bottomRight: r,
                  ),
                ),
                child: Text(m.text, style: theme.textTheme.bodyMedium?.copyWith(color: fg)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.message});

  final ThreadMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final time = clockTime(message.time, message.when);
    final text = message.text.isEmpty ? message.author : message.text;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 24),
      child: Text(
        time.isEmpty ? text : '$text  ·  $time',
        textAlign: TextAlign.center,
        style: theme.textTheme.labelSmall?.copyWith(color: cs.outline),
      ),
    );
  }
}

/// Asks for an optional closing note. Returns null when cancelled.
class _CloseDialog extends StatefulWidget {
  const _CloseDialog();

  @override
  State<_CloseDialog> createState() => _CloseDialogState();
}

class _CloseDialogState extends State<_CloseDialog> {
  final _note = TextEditingController();

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Close this conversation?'),
        content: SizedBox(
          width: 420,
          child: TextField(
            controller: _note,
            minLines: 1,
            maxLines: 4,
            decoration: const InputDecoration(labelText: 'Closing note (optional)'),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, _note.text.trim()), child: const Text('Close it')),
        ],
      );
}
