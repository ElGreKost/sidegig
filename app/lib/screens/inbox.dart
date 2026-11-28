// Inbox: what needs the person now. "To answer" comes from the inbox tool;
// "Asked by me" joins the answers waiting for the person with every other
// gig they (or one of their agents) asked that is not closed yet.

import 'package:flutter/material.dart';

import '../board.dart';
import '../models.dart';
import '../widgets.dart';

class InboxPane extends StatelessWidget {
  const InboxPane({
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

  static String _titleFor(String key) {
    final words = key.replaceAll('_', ' ').trim();
    if (words.isEmpty) return key;
    return words[0].toUpperCase() + words.substring(1);
  }

  List<Gig> _askedByMe() {
    final me = board.me;
    final seen = <String>{};
    final out = <Gig>[];
    for (final g in [...board.inbox.answered, ...board.gigs.where((g) => me.owns(g.asker) && !g.isClosed)]) {
      if (seen.add(g.id)) out.add(g);
    }
    out.sort(Gig.byNewest);
    return out;
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final toAnswer = [...board.inbox.toAnswer]..sort(Gig.byNewest);
        final sections = <(String, String?, List<Gig>)>[
          ('To answer', 'Addressed to you or a group you are in.', toAnswer),
          ('Asked by me', 'Answers waiting for you, and questions still open.', _askedByMe()),
          for (final e in board.inbox.other.entries) (_titleFor(e.key), null, e.value),
        ];

        Widget body;
        if (board.loadingLists && board.gigs.isEmpty) {
          body = const Center(
            child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
          );
        } else if (board.listError != null && board.gigs.isEmpty) {
          body = EmptyState(
            icon: Icons.cloud_off_outlined,
            title: 'Could not load your inbox',
            body: board.listError,
            action: OutlinedButton(onPressed: board.refreshLists, child: const Text('Try again')),
          );
        } else {
          body = RefreshIndicator(
            onRefresh: board.refreshLists,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(0, 0, 0, 96),
              children: [
                for (final (title, subtitle, gigs) in sections) ...[
                  SectionHeader(title, count: gigs.length, subtitle: subtitle),
                  if (gigs.isEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                      child: Text(
                        'Nothing here right now.',
                        style: Theme.of(context)
                            .textTheme
                            .bodySmall
                            ?.copyWith(color: Theme.of(context).colorScheme.outline),
                      ),
                    )
                  else
                    for (final g in gigs)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 1),
                        child: GigTile(
                          gig: g,
                          selected: g.id == selectedId,
                          onTap: () => onOpen(g.id),
                        ),
                      ),
                ],
              ],
            ),
          );
        }

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [header, Expanded(child: body)],
        );
      },
    );
  }
}
