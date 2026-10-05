import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuPortal, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuShortcut, DropdownMenuSub, DropdownMenuSubContent,
  DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

/** A menu that uses every part the wrapper offers (the app itself uses only items). */
function FullMenu ({ onDelete }: { onDelete: () => void }) {
  const [hidden, setHidden] = useState(false);
  const [sort, setSort] = useState('name');
  return (
    <DropdownMenu>
      <DropdownMenuTrigger>Options</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel inset>View</DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuCheckboxItem checked={hidden} onCheckedChange={setHidden} onSelect={(e) => e.preventDefault()}>
            Show hidden files
          </DropdownMenuCheckboxItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={sort} onValueChange={setSort}>
          <DropdownMenuRadioItem value="name" onSelect={(e) => e.preventDefault()}>By name</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="size" onSelect={(e) => e.preventDefault()}>By size</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger inset>More</DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent>
              <DropdownMenuItem variant="destructive" onClick={onDelete}>
                Delete
                <DropdownMenuShortcut>Del</DropdownMenuShortcut>
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

describe('DropdownMenu wrapper', () => {
  it('toggles checkbox and radio items and reaches a submenu item', async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(<FullMenu onDelete={onDelete} />);

    await user.click(screen.getByRole('button', { name: 'Options' }));
    expect(screen.getByText('View')).toHaveAttribute('data-inset', 'true');
    expect(screen.getByRole('separator')).toBeInTheDocument();

    const hidden = screen.getByRole('menuitemcheckbox', { name: 'Show hidden files' });
    expect(hidden).toHaveAttribute('aria-checked', 'false');
    await user.click(hidden);
    expect(hidden).toHaveAttribute('aria-checked', 'true');

    expect(screen.getByRole('menuitemradio', { name: 'By name' })).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('menuitemradio', { name: 'By size' }));
    expect(screen.getByRole('menuitemradio', { name: 'By size' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitemradio', { name: 'By name' })).toHaveAttribute('aria-checked', 'false');

    // Open the submenu from the keyboard.
    const more = screen.getByRole('menuitem', { name: 'More' });
    more.focus();
    await user.keyboard('{ArrowRight}');
    const del = await screen.findByRole('menuitem', { name: /Delete/ });
    expect(del).toHaveAttribute('data-variant', 'destructive');
    expect(screen.getByText('Del')).toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();
    // A pointer move would leave the submenu, so pick the item with the keyboard.
    await waitFor(() => expect(del).toHaveFocus());
    await user.keyboard('{Enter}');
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
