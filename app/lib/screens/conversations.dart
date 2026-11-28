// Conversations: every gig on the board, newest activity first, with status
// filters and a "Mine" toggle.

import 'package:flutter/material.dart';

import '../board.dart';
import '../models.dart';
import '../widgets.dart';

enum _Filter { all, open, answered, closed }

class ConversationsPane extends StatefulWidget {
  const ConversationsPane({
    super.key,
    required this.board,
    required this.header,
    required this.onOpen,
    this.selectedId,
  });

  final Board board;
  final Widget header;
  final ValueChanged<String> onOpen;
  final String? selectedId;

  @override
  State<ConversationsPane> createState() => _ConversationsPaneState();
}

class _ConversationsPaneState extends State<ConversationsPane> {
  _Filter _filter = _Filter.all;
  bool _mine = false;

  bool _isMine(Gig g, Me me) => me.owns(g.asker) || me.owns(g.toAgent) || me.owns(g.claimedBy);

  bool _matches(Gig g) => switch (_filter) {
        _Filter.all => true,
        // Claimed and stale gigs are still waiting for an answer.
        _Filter.open => g.status == 'open' || g.status == 'claimed' || g.status == 'stale',
        _Filter.answered => g.status == 'answered',
        _Filter.closed => g.status == 'closed',
      };

  @override
  Widget build(BuildContext context) {
    final board = widget.board;
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final me = board.me;
        final gigs = board.gigs.where((g) => _matches(g) && (!_mine || _isMine(g, me))).toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            widget.header,
            _filters(context),
            const SizedBox(height: 4),
            Expanded(child: _list(context, gigs, me)),
          ],
        );
      },
    );
  }

  Widget _filters(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    const labels = {
      _Filter.all: 'All',
      _Filter.open: 'Open',
      _Filter.answered: 'Answered',
      _Filter.closed: 'Closed',
    };
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Row(
        children: [
          for (final f in _Filter.values) ...[
            ChoiceChip(
              label: Text(labels[f]!),
              selected: _filter == f,
              showCheckmark: false,
              visualDensity: VisualDensity.compact,
              onSelected: (_) => setState(() => _filter = f),
            ),
            const SizedBox(width: 6),
          ],
          Container(
            width: 1,
            height: 20,
            margin: const EdgeInsets.symmetric(horizontal: 6),
            color: cs.outlineVariant,
          ),
          FilterChip(
            label: const Text('Mine'),
            selected: _mine,
            visualDensity: VisualDensity.compact,
            onSelected: (v) => setState(() => _mine = v),
          ),
        ],
      ),
    );
  }

  Widget _list(BuildContext context, List<Gig> gigs, Me me) {
    final board = widget.board;
    if (board.loadingLists && board.gigs.isEmpty) {
      return const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)));
    }
    if (board.listError != null && board.gigs.isEmpty) {
      return EmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load the board',
        body: board.listError,
        action: OutlinedButton(onPressed: board.refreshLists, child: const Text('Try again')),
      );
    }
    if (gigs.isEmpty) {
      return EmptyState(
        icon: Icons.forum_outlined,
        title: board.gigs.isEmpty ? 'No conversations yet' : 'Nothing matches these filters',
        body: board.gigs.isEmpty ? 'Start one with New conversation.' : null,
      );
    }
    return RefreshIndicator(
      onRefresh: board.refreshLists,
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(8, 4, 8, 96),
        itemCount: gigs.length,
        separatorBuilder: (_, _) => const SizedBox(height: 2),
        itemBuilder: (context, i) {
          final g = gigs[i];
          return GigTile(
            gig: g,
            selected: g.id == widget.selectedId,
            mine: _isMine(g, me),
            onTap: () => widget.onOpen(g.id),
          );
        },
      ),
    );
  }
}
